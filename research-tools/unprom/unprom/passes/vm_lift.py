"""Full lift of the Prometheus ``Vmify`` VM back to structured Lua.

Pipeline for the container function (and, recursively, every nested closure):

1. flatten the ``if pos < K`` search tree into an ordered block list
   (:mod:`unprom.vm`);
2. **symbolically execute** each block over the register file, turning
   ``rT="foo"; rX=ENV[rT]`` back into ``foo``, ``rX = a + b`` into an
   expression, ``createClosureN(id,{..})`` into a nested ``function`` etc., and
   dropping the scope-cleanup ``reg = nil`` noise;
3. classify each block's terminator from the symbolic value of ``pos`` /
   ``ret`` (goto / branch / return);
4. **structure** the resulting CFG into ``if`` / ``elseif`` / ``else``,
   ``while`` and numeric ``for`` using dominator / post-dominator info.

If a function cannot be structured cleanly it is left for the de-flattener
(:mod:`unprom.passes.devirtualize`) and a note is emitted.  Enabled unless the
``devirt`` option is ``"off"`` or ``"deflatten"``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .. import ast_nodes as A
from ..ast_nodes import clone
from ..astutil import as_int, as_string, unparen
from ..evaluator import const_value, value_to_node
from ..vm import Container, find_container, flatten_blocks, id_to_index


# ======================================================================
# Terminators
# ======================================================================
class Term:
    def succs(self) -> List[int]:
        return []


class TGoto(Term):
    def __init__(self, target: int):
        self.target = target

    def succs(self):
        return [self.target]

    def __repr__(self):
        return f"goto {self.target}"


class TBranch(Term):
    def __init__(self, cond: A.Node, t: int, f: int):
        self.cond, self.t, self.f = cond, t, f

    def succs(self):
        return [self.t, self.f]

    def __repr__(self):
        return f"branch ? {self.t} : {self.f}"


class TReturn(Term):
    def __init__(self, values: List[A.Node]):
        self.values = values

    def __repr__(self):
        return f"return ({len(self.values)})"


class TRaw(Term):
    """Could not classify - keep the block's tail statements verbatim."""
    def __init__(self, stmts: List[A.Node]):
        self.stmts = stmts

    def __repr__(self):
        return "raw-tail"


class LiftedBlock:
    def __init__(self, index: int):
        self.index = index
        self.body: List[A.Node] = []
        self.term: Term = TRaw([])

    def succs(self) -> List[int]:
        t = self.term
        if isinstance(t, TGoto):
            return [t.target]
        if isinstance(t, TBranch):
            return [t.t, t.f]
        return []


class Unsupported(Exception):
    pass


# ======================================================================
# Function frame
# ======================================================================
class Frame:
    def __init__(self, parent: Optional["Frame"]):
        self.parent = parent
        self.params: List[str] = []
        self.is_vararg = False
        self.is_top = False
        self.upvalues: Dict[int, str] = {}       # upvalue id -> name visible here
        self.var_regs: Set[str] = set()
        self.param_map: Dict[str, str] = {}
        self.declared_vars: Set[str] = set()


# ======================================================================
# The lifter
# ======================================================================
class Lifter:
    def __init__(self, chunk: A.Chunk, c: Container, ctx):
        self.chunk = chunk
        self.c = c
        self.ctx = ctx
        self.blocks, self.seps = flatten_blocks(c.while_stmt)
        self.nblocks = len(self.blocks)
        self._local_pool_n = 0
        self._closure_cache: Dict[tuple, A.Function] = {}
        self._lifting: Set[tuple] = set()
        self._free_upval_names = set(c.free_upval_names)
        if c.free_upval_name:
            self._free_upval_names.add(c.free_upval_name)

    # -- name helpers ------------------------------------------------
    def new_local(self) -> str:
        self._local_pool_n += 1
        return f"L{self._local_pool_n}"

    def idx_of(self, block_id: int) -> int:
        return id_to_index(block_id, self.seps)

    # -- entry -----------------------------------------------------
    def lift(self) -> A.Function:
        root = Frame(None)
        root.is_top = True
        entry = self.idx_of(self.c.start_id) if self.c.start_id is not None else 0
        body = self.lift_function_body(entry, root, is_top=True)
        return A.Function(params=[], is_vararg=True, body=body)

    # -- per-function --------------------------------------------
    def lift_function_body(self, entry: int, frame: Frame, is_top=False) -> A.Block:
        c = self.c
        regset = set(c.reg_names)
        reach = self._reachable(entry, frame)

        # Which register(s) carry a real (cross-block / loop-carried) variable?
        param_map: Dict[str, str] = {}
        self._scan_params(entry, frame, param_map)
        var_regs = self._classify_variables(reach, regset, param_map)
        frame.var_regs = var_regs
        frame.param_map = param_map
        frame.declared_vars = set()

        lifted: Dict[int, LiftedBlock] = {}
        for bi in reach:
            lifted[bi] = self._exec_block(bi, frame, entry)

        struct = Structurer(lifted, entry, frame, self.ctx)
        stmts = struct.run()

        for _ in range(4):
            if not _prune_dead_stores(stmts, set()):
                break

        decl = sorted(
            {d for d in frame.declared_vars if _name_used(stmts, d)},
            key=_reg_sort_key)
        if decl:
            stmts.insert(0, A.LocalAssign(
                targets=[A.Name(name=d) for d in decl], values=[],
                attribs=[None] * len(decl)))
        return A.Block(stmts=stmts)

    # -- register classification ---------------------------------
    def _scan_params(self, entry: int, frame: Frame, param_map: Dict[str, str]):
        """Read the entry block prologue: `pReg = args[i]` (or captured/vararg)."""
        c = self.c
        for s in self.blocks[entry]:
            if not (isinstance(s, A.Assign) and len(s.targets) == 1
                    and isinstance(unparen(s.targets[0]), A.Name)):
                continue
            nm = unparen(s.targets[0]).name
            if not s.values:
                continue
            v = unparen(s.values[0])
            if isinstance(v, A.Index) and _is_name(v.obj, c.args_name):
                k = as_int(v.key)
                if k is not None:
                    param_map[nm] = _param_name(frame, k)
            elif _is_vararg_pack(v, c) or _is_name(v, c.args_name):
                frame.is_vararg = True

    def _classify_variables(self, reach, regset, param_map) -> Set[str]:
        """A register is a variable if some block reads it before writing it
        (its definition lives in another block / a previous iteration)."""
        variables: Set[str] = set()
        c = self.c
        for bi in reach:
            written: Set[str] = set()
            for s in self.blocks[bi]:
                for nm in _reg_reads_of(s, regset):
                    if nm in (c.pos_name, c.ret_name):
                        continue
                    if nm not in written and nm not in param_map:
                        variables.add(nm)
                if isinstance(s, A.Assign) and len(s.targets) == 1 \
                        and isinstance(unparen(s.targets[0]), A.Name):
                    written.add(unparen(s.targets[0]).name)
        return variables

    def _reachable(self, entry: int, frame: Frame) -> List[int]:
        """Symbolically resolve each block's `pos` terminator to discover the
        successor set (a cheap dummy frame is enough - successor resolution
        doesn't depend on variable classification)."""
        dummy = Frame(None)
        seen: Set[int] = set()
        order: List[int] = []
        stack = [entry]
        while stack:
            b = stack.pop()
            if b in seen or b < 0 or b >= self.nblocks:
                continue
            seen.add(b)
            order.append(b)
            try:
                term = self._exec_block(b, dummy, entry).term
            except Unsupported:
                continue
            for s in term.succs():
                if s not in seen:
                    stack.append(s)
        return order

    # -- symbolic execution of one block --------------------------
    def _exec_block(self, bi: int, frame: Frame, entry: int) -> LiftedBlock:
        lb = LiftedBlock(bi)
        c = self.c
        # seed: parameter registers resolve to their parameter name everywhere
        state: Dict[str, A.Node] = {
            r: A.Name(name=p) for r, p in getattr(frame, "param_map", {}).items()}
        raw = list(self.blocks[bi])
        raw = _drop_trailing_reg_nils(raw, c.reg_names, c.pos_name)
        if bi == entry:
            raw = self._strip_prologue(raw, frame)

        for i, s in enumerate(raw):
            future = raw[i + 1:]
            if isinstance(s, A.CallStat):
                lb.body.append(A.CallStat(call=self._subst(s.call, state, frame)))
                continue
            if not isinstance(s, A.Assign):
                raise Unsupported(f"block {bi}: {s.kind} statement")

            if len(s.targets) != 1:
                rhs = self._subst(s.values[0], state, frame) if s.values else A.Nil()
                names = [self.new_local() for _ in s.targets]
                lb.body.append(A.LocalAssign(
                    targets=[A.Name(name=n) for n in names], values=[rhs],
                    attribs=[None] * len(names)))
                for tgt, nm in zip(s.targets, names):
                    if isinstance(unparen(tgt), A.Name):
                        state[unparen(tgt).name] = A.Name(name=nm)
                continue

            tgt = unparen(s.targets[0])
            val = self._subst(s.values[0], state, frame) if s.values else A.Nil()

            if isinstance(tgt, A.Name):
                nm = tgt.name
                # `w` (pos) and `h` (ret) are reused as scratch registers, so we
                # just let them flow through `state` and read the final value
                # off the end of the block.
                if nm in (c.pos_name, c.ret_name):
                    state[nm] = val
                    continue
                # recognise globals / varargs / params / upvalue cells /
                # nested closures first - these must not be masked by the
                # variable-register path.
                if self._special_bind(nm, val, state, frame, lb):
                    continue
                if nm in getattr(frame, "var_regs", ()) and nm not in \
                        getattr(frame, "param_map", {}):
                    frame.declared_vars.add(nm)
                    lb.body.append(A.Assign(targets=[A.Name(name=nm)], values=[val]))
                    # keep the full symbolic value too, so the block terminator
                    # and later in-block reads resolve completely (register
                    # reuse means the same name can be a plain temp here).  Only
                    # withhold it when it holds a call, to avoid duplicating a
                    # side effect.
                    state[nm] = A.Name(name=nm) if _contains_call(val) else val
                    continue
                self._bind_register(nm, val, state, frame, lb, future)
                continue

            if isinstance(tgt, A.Index):
                self._store_index(tgt, val, state, frame, lb)
                continue

            raise Unsupported(f"block {bi}: assignment to {tgt.kind}")

        lb.term = self._classify_term(state.get(c.pos_name), state.get(c.ret_name),
                                      bi)
        # lift any factory calls that survived inside emitted statements / return
        lb.body = [self._lift_nested(s, state, frame) for s in lb.body]
        if isinstance(lb.term, TReturn):
            lb.term.values = [self._lift_nested(v, state, frame)
                              for v in lb.term.values]
        return lb

    def _lift_nested(self, node, state, frame):
        """Replace `factory(id, {ups})` occurrences anywhere in ``node`` with the
        lifted nested function."""
        if not isinstance(node, A.Node):
            return node
        if self._is_factory_call(node):
            fn = self._lift_factory(node, state, frame)
            if fn is not None:
                return fn
        if isinstance(node, A.Call) and self._is_factory_call(node.func):
            fn = self._lift_factory(unparen(node.func), state, frame)
            if fn is not None:
                return A.Call(func=A.Paren(inner=fn),
                              args=[self._lift_nested(a, state, frame)
                                    for a in node.args])
        for fname, value in list(A.iter_fields(node)):
            if isinstance(value, A.Node):
                setattr(node, fname, self._lift_nested(value, state, frame))
            elif isinstance(value, list):
                setattr(node, fname, [
                    self._lift_nested(x, state, frame) if isinstance(x, A.Node)
                    else (tuple(self._lift_nested(y, state, frame)
                                if isinstance(y, A.Node) else y for y in x)
                          if isinstance(x, tuple) else x)
                    for x in value])
        return node

    # -- register binding --------------------------------------
    def _special_bind(self, nm, val, state, frame, lb) -> bool:
        """Recognisers that apply regardless of variable-register status.
        Returns True when it has fully handled the assignment."""
        c = self.c
        val = unparen(val)

        # scope-cleanup: reg = freeUpvalue(reg) / freeUpvalueList(reg)  -> drop
        if isinstance(val, A.Call) and isinstance(unparen(val.func), A.Name) \
                and unparen(val.func).name in self._free_upval_names:
            return True

        # global read:  ENV[<str>]
        if isinstance(val, A.Index) and _is_name(val.obj, c.env_name):
            key = as_string(val.key)
            if key is not None:
                state[nm] = A.Name(name=key)
                return True

        if _is_name(val, c.args_name) or _is_vararg_pack(val, c):
            state[nm] = A.Vararg()
            return True

        # parameter:  args[i]
        if isinstance(val, A.Index) and _is_name(val.obj, c.args_name):
            k = as_int(val.key)
            if k is not None:
                state[nm] = A.Name(name=_param_name(frame, k))
                return True

        # alloc upvalue cell:  X = allocUpval()
        if isinstance(val, A.Call) and _is_name(val.func, c.alloc_upval_name) \
                and not val.args:
            state[nm] = _UpvalCell(self.new_local())
            return True

        # upvalue read:  UPV[idx]
        if isinstance(val, A.Index) and _is_name(val.obj, c.upvals_table_name):
            resolved = self._resolve_upval_id(val.key, state, frame)
            if resolved is not None:
                state[nm] = A.Name(name=resolved)
                return True

        # nested closure:  X = factory(id, {ups})  [ (args) ]
        clo = self._as_closure(val, state, frame)
        if clo is not None:
            if nm in getattr(frame, "var_regs", ()):
                frame.declared_vars.add(nm)
                lb.body.append(A.Assign(targets=[A.Name(name=nm)], values=[clo]))
                state[nm] = A.Name(name=nm)
            else:
                state[nm] = clo
            return True
        return False

    def _bind_register(self, nm, val, state, frame, lb, future):
        val = unparen(val)
        if self._special_bind(nm, val, state, frame, lb):
            return

        # bare call:  rX = f(args)
        if isinstance(val, (A.Call, A.MethodCall)):
            reads = _count_name(future, nm)
            if reads == 0:
                lb.body.append(A.CallStat(call=val))     # result unused -> statement
                return
            if reads == 1:
                state[nm] = val                          # single use -> inline
                return
            t = self.new_local()
            lb.body.append(A.LocalAssign(targets=[A.Name(name=t)], values=[val],
                                         attribs=[None]))
            state[nm] = A.Name(name=t)
            return

        # expression that *contains* a call and is read more than once: pin it
        if _contains_call(val) and _count_name(future, nm) > 1:
            t = self.new_local()
            lb.body.append(A.LocalAssign(targets=[A.Name(name=t)], values=[val],
                                         attribs=[None]))
            state[nm] = A.Name(name=t)
            return

        state[nm] = val

    def _store_index(self, tgt: A.Index, val, state, frame, lb):
        c = self.c
        # global write
        if _is_name(tgt.obj, c.env_name):
            key = as_string(tgt.key)
            if key is not None:
                lb.body.append(A.Assign(targets=[A.Name(name=key)], values=[val]))
                return
        # upvalue write
        if _is_name(tgt.obj, c.upvals_table_name):
            resolved = self._resolve_upval_id(tgt.key, state, frame, creating=True)
            if resolved is not None:
                if resolved.startswith("\0"):
                    real = resolved[1:]
                    lb.body.append(A.LocalAssign(targets=[A.Name(name=real)],
                                                 values=[val], attribs=[None]))
                else:
                    lb.body.append(A.Assign(targets=[A.Name(name=resolved)],
                                            values=[val]))
                return
        base = self._subst(tgt.obj, state, frame)
        key = self._subst(tgt.key, state, frame)
        lb.body.append(A.Assign(targets=[A.Index(obj=base, key=key, dot=tgt.dot)],
                                values=[val]))

    def _resolve_upval_id(self, key_expr, state, frame, creating=False):
        k = self._subst(key_expr, state, frame)
        k = unparen(k)
        if isinstance(k, _UpvalCell):
            if creating and not k.declared:
                k.declared = True
                return "\0" + k.name
            return k.name
        if isinstance(k, A.Name) and isinstance(state.get(k.name), _UpvalCell):
            cell = state[k.name]
            if creating and not cell.declared:
                cell.declared = True
                return "\0" + cell.name
            return cell.name
        # currentUpvalues[j]
        if isinstance(k, A.Index) and _is_name(k.obj, self.c.upvals_name):
            j = as_int(k.key)
            if j is not None and j in frame.upvalues:
                return frame.upvalues[j]
        return None

    # -- closure recognition -----------------------------------
    def _is_factory_call(self, node) -> bool:
        node = unparen(node)
        if not isinstance(node, A.Call) or len(node.args) != 2:
            return False
        f = unparen(node.func)
        if not isinstance(f, A.Name):
            return False
        if f.name not in self.c.factory_names:
            # name not captured: fall back to the (id:int, {..}) shape
            if as_int(node.args[0]) is None:
                return False
        return as_int(node.args[0]) is not None \
            and isinstance(unparen(node.args[1]), A.Table)

    def _lift_factory(self, call, state, frame) -> Optional[A.Function]:
        bid = as_int(call.args[0])
        upt = unparen(call.args[1])
        if bid is None or not isinstance(upt, A.Table):
            return None
        ups = tuple(self._name_for_upval_entry(ent, state, frame)
                    for _k, _key, ent in upt.fields)
        key = (bid, ups)
        cached = self._closure_cache.get(key)
        if cached is not None:
            return clone(cached)
        if key in self._lifting:            # guard against pathological recursion
            return A.Function(params=[], is_vararg=True, body=A.Block(stmts=[]))
        self._lifting.add(key)
        child = Frame(frame)
        for k, nm in enumerate(ups, start=1):
            child.upvalues[k] = nm
        body = self.lift_function_body(self.idx_of(bid), child)
        fn = A.Function(params=[A.Name(name=p) for p in child.params],
                        is_vararg=child.is_vararg, body=body)
        self._lifting.discard(key)
        self._closure_cache[key] = fn
        return clone(fn)

    def _as_closure(self, val, state, frame):
        """`factory(id, {ups})`            -> function ... end
           `factory(id, {ups})(a, b, ...)` -> (function ... end)(a, b, ...)"""
        val = unparen(val)
        if self._is_factory_call(val):
            return self._lift_factory(val, state, frame)
        if isinstance(val, A.Call) and self._is_factory_call(val.func):
            fn = self._lift_factory(unparen(val.func), state, frame)
            if fn is not None:
                return A.Call(func=A.Paren(inner=fn),
                              args=[self._subst(a, state, frame) for a in val.args])
        return None

    def _name_for_upval_entry(self, ent, state, frame) -> str:
        ent = unparen(self._subst(ent, state, frame))
        if isinstance(ent, _UpvalCell):
            return ent.name
        if isinstance(ent, A.Name):
            return ent.name
        if isinstance(ent, A.Index) and _is_name(ent.obj, self.c.upvals_name):
            j = as_int(ent.key)
            if j is not None and j in frame.upvalues:
                return frame.upvalues[j]
        if isinstance(ent, A.Call) and _is_name(ent.func, self.c.alloc_upval_name):
            return self.new_local()
        return self.new_local()

    def _strip_prologue(self, raw: List[A.Node], frame: Frame) -> List[A.Node]:
        """Drop the entry-block statements that only bind parameters."""
        c = self.c
        out = []
        for s in raw:
            if isinstance(s, A.Assign) and len(s.targets) == 1 \
                    and isinstance(unparen(s.targets[0]), A.Name) and s.values:
                nm = unparen(s.targets[0]).name
                v = unparen(s.values[0])
                if nm in frame.param_map and isinstance(v, A.Index) \
                        and _is_name(v.obj, c.args_name):
                    continue
                if _is_vararg_pack(v, c) or _is_name(v, c.args_name):
                    frame.is_vararg = True
                    continue
            out.append(s)
        return out

    # -- terminator classification ----------------------------
    def _classify_term(self, pos_expr, ret_expr, bi=-1) -> Term:
        if pos_expr is None:
            return TRaw([])
        pe = unparen(pos_expr)
        if _is_exit(pe, self.c):
            return TReturn(self._return_values(ret_expr) if ret_expr is not None
                           else [])
        n = as_int(pe)
        if n is not None:
            return TGoto(self.idx_of(n))
        br = _as_branch(pe)
        if br is not None:
            cond, a, b = br
            return TBranch(cond, self.idx_of(a), self.idx_of(b))
        from ..unparser import Unparser
        try:
            txt = Unparser()._expr(pe)
        except Exception:  # noqa: BLE001
            txt = pe.kind
        raise Unsupported(f"block {bi}: unclassifiable pos expression -> {txt}")

    def _return_values(self, ret_expr) -> List[A.Node]:
        r = unparen(ret_expr)
        if isinstance(r, A.Table):
            out = []
            for _k, _key, v in r.fields:
                v = unparen(v)
                if isinstance(v, A.Call) and _is_name(v.func, self.c.unpack_name) \
                        and len(v.args) == 1:
                    inner = unparen(v.args[0])
                    if isinstance(inner, A.Table):
                        # unpack({ a, b, ... })  ->  a, b, ...
                        out.extend(unparen(f[2]) for f in inner.fields)
                    else:
                        out.append(v.args[0])   # return unpack(x) -> return x...
                else:
                    out.append(v)
            return out
        return [r]

    # -- substitution -----------------------------------------
    def _subst(self, expr: A.Node, state: Dict[str, A.Node], frame: Frame,
               depth=0) -> A.Node:
        if depth > 60 or expr is None:
            return expr
        if isinstance(expr, A.Name):
            if expr.name in state:
                v = state[expr.name]
                if isinstance(v, _UpvalCell):
                    return A.Name(name=v.name)
                return clone(v)
            return A.Name(name=expr.name, binding=expr.binding)
        if isinstance(expr, _UpvalCell):
            return A.Name(name=expr.name)
        new = clone(expr)
        self._subst_children(new, state, frame, depth)
        return _simplify(new)

    def _subst_children(self, node: A.Node, state, frame, depth):
        for fname, value in list(A.iter_fields(node)):
            if isinstance(value, A.Node):
                setattr(node, fname, self._subst(value, state, frame, depth + 1))
            elif isinstance(value, list):
                out = []
                for it in value:
                    if isinstance(it, A.Node):
                        out.append(self._subst(it, state, frame, depth + 1))
                    elif isinstance(it, tuple):
                        out.append(tuple(
                            self._subst(x, state, frame, depth + 1)
                            if isinstance(x, A.Node) else x for x in it))
                    else:
                        out.append(it)
                setattr(node, fname, out)


class _UpvalCell:
    __slots__ = ("name", "declared", "line")

    def __init__(self, name: str):
        self.name = name
        self.declared = False
        self.line = 0

    @property
    def kind(self):
        return "Name"


# ======================================================================
# Helpers
# ======================================================================
def _is_name(node, name) -> bool:
    if name is None:
        return False
    node = unparen(node)
    return isinstance(node, A.Name) and node.name == name


def _reg_reads_of(s: A.Node, regset: Set[str]) -> List[str]:
    """Register names *read* by statement ``s`` (LHS bare-name targets excluded)."""
    out: List[str] = []
    if isinstance(s, A.Assign):
        for t in s.targets:
            t = unparen(t)
            if isinstance(t, A.Index):
                out += [n.name for n in A.walk(t)
                        if isinstance(n, A.Name) and n.name in regset]
        for v in s.values:
            out += [n.name for n in A.walk(v)
                    if isinstance(n, A.Name) and n.name in regset]
    else:
        out += [n.name for n in A.walk(s)
                if isinstance(n, A.Name) and n.name in regset]
    return out


def _reg_sort_key(name: str):
    m = "".join(ch for ch in name if ch.isdigit())
    return (0, int(m)) if m else (1, name)


def _numbers_in(expr) -> List[int]:
    out = []
    for n in A.walk(expr):
        if isinstance(n, A.Number):
            iv = as_int(n)
            if iv is not None:
                out.append(iv)
    return out


def _drop_trailing_reg_nils(raw, reg_names, pos_name):
    regset = set(reg_names)
    end = len(raw)
    # keep terminator (pos = ...) but drop a run of `regN = nil` just before it
    term_i = end
    for i in range(end - 1, -1, -1):
        s = raw[i]
        if isinstance(s, A.Assign) and len(s.targets) == 1 \
                and _is_name(s.targets[0], pos_name):
            term_i = i
            break
    keep = []
    for i, s in enumerate(raw):
        if i < term_i and isinstance(s, A.Assign) and len(s.targets) == 1:
            t = unparen(s.targets[0])
            if isinstance(t, A.Name) and t.name in regset and len(s.values) == 1 \
                    and isinstance(unparen(s.values[0]), A.Nil):
                continue
        keep.append(s)
    return keep


def _is_vararg_pack(val, c: Container) -> bool:
    val = unparen(val)
    if not isinstance(val, A.Table) or len(val.fields) != 1:
        return False
    v = unparen(val.fields[0][2])
    if not isinstance(v, A.Call) or not _is_name(v.func, c.select_name):
        return False
    return True


def _param_name(frame: Frame, k: int) -> str:
    while len(frame.params) < k:
        frame.params.append(f"a{len(frame.params) + 1}")
    return frame.params[k - 1]


def _is_exit(pos_expr, c: Container) -> bool:
    if pos_expr is None:
        return False
    pe = unparen(pos_expr)
    if isinstance(pe, A.Nil):
        return True
    if isinstance(pe, A.Index) and _is_name(pe.obj, c.env_name):
        # pos = ENV["<random>"]  -> nil at runtime
        return as_string(pe.key) is not None
    return False


def _as_branch(pe: A.Node):
    """`(C and A) or B` with A,B integer literals -> (C, A, B)."""
    pe = unparen(pe)
    if not (isinstance(pe, A.BinOp) and pe.op == "or"):
        return None
    b = as_int(pe.right)
    land = unparen(pe.left)
    if b is None or not (isinstance(land, A.BinOp) and land.op == "and"):
        return None
    a = as_int(land.right)
    if a is None:
        return None
    return land.left, a, b


def _contains_call(node) -> bool:
    for n in A.walk(node):
        if isinstance(n, (A.Call, A.MethodCall)):
            return True
    return False


def _count_name(stmts, name) -> int:
    total = 0
    for s in stmts:
        for n in A.walk(s):
            if isinstance(n, A.Name) and n.name == name:
                total += 1
    return total


def _reads_more_than_once(stmts, name) -> bool:
    return _count_name(stmts, name) > 1


def _simplify(node: A.Node) -> A.Node:
    # ({ expr })[1]  ->  (expr)
    if isinstance(node, A.Index):
        base = unparen(node.obj)
        k = as_int(node.key)
        if isinstance(base, A.Table) and k == 1 and len(base.fields) == 1 \
                and base.fields[0][0] == "pos":
            inner = base.fields[0][2]
            return A.Paren(inner=inner) if isinstance(inner, (A.Call, A.MethodCall)) \
                else inner
    if isinstance(node, (A.BinOp, A.UnOp)):
        ok, v = const_value(node)
        if ok and isinstance(v, (bool, float, str)):
            try:
                return value_to_node(v)
            except TypeError:
                pass
    return node


# ======================================================================
# CFG structuring
# ======================================================================
class Structurer:
    def __init__(self, blocks: Dict[int, LiftedBlock], entry: int, frame: Frame,
                 ctx):
        self.blocks = blocks
        self.entry = entry
        self.frame = frame
        self.ctx = ctx
        self.nodes = sorted(blocks)
        self.preds: Dict[int, List[int]] = {n: [] for n in self.nodes}
        for n in self.nodes:
            for s in blocks[n].succs():
                if s in self.preds:
                    self.preds[s].append(n)
        self.idom = self._dominators()
        self.loop_headers = self._find_loop_headers()

    # -- dominators (Cooper-Harvey-Kennedy) -------------------
    def _rpo(self) -> List[int]:
        order, seen = [], set()

        def dfs(n):
            seen.add(n)
            for s in self.blocks[n].succs():
                if s in self.blocks and s not in seen:
                    dfs(s)
            order.append(n)

        dfs(self.entry)
        order.reverse()
        return order

    def _dominators(self) -> Dict[int, int]:
        rpo = self._rpo()
        pos = {n: i for i, n in enumerate(rpo)}
        idom = {self.entry: self.entry}

        def inter(a, b):
            while a != b:
                while pos[a] > pos[b]:
                    a = idom[a]
                while pos[b] > pos[a]:
                    b = idom[b]
            return a

        changed = True
        while changed:
            changed = False
            for n in rpo:
                if n == self.entry:
                    continue
                ps = [p for p in self.preds[n] if p in idom]
                if not ps:
                    continue
                new = ps[0]
                for p in ps[1:]:
                    new = inter(p, new)
                if idom.get(n) != new:
                    idom[n] = new
                    changed = True
        return idom

    def _dominates(self, a: int, b: int) -> bool:
        while b != self.entry:
            if a == b:
                return True
            b = self.idom.get(b, self.entry)
        return a == self.entry

    def _find_loop_headers(self) -> Dict[int, Set[int]]:
        headers: Dict[int, Set[int]] = {}
        for n in self.nodes:
            for s in self.blocks[n].succs():
                if s in self.blocks and self._dominates(s, n):
                    body = self._natural_loop(s, n)
                    headers.setdefault(s, set()).update(body)
        return headers

    def _natural_loop(self, header: int, tail: int) -> Set[int]:
        body = {header, tail}
        stack = [tail]
        while stack:
            n = stack.pop()
            for p in self.preds[n]:
                if p not in body:
                    body.add(p)
                    stack.append(p)
        return body

    # -- structuring -----------------------------------------
    def run(self) -> List[A.Node]:
        self._open_loops: Set[int] = set()
        return self._region(self.entry, set())

    def _region(self, node: Optional[int], stop: Set[int],
                force_first: bool = False) -> List[A.Node]:
        out: List[A.Node] = []
        cur = node
        guard = 0
        while cur is not None:
            if cur in stop and not force_first:
                break
            force_first = False
            guard += 1
            if guard > len(self.blocks) * 4 + 10:
                raise Unsupported("structuring did not converge")

            if cur in self.loop_headers and cur not in self._open_loops:
                loop_node, follow = self._emit_loop(cur, stop)
                if loop_node is not None:
                    out.append(loop_node)
                cur = follow
                continue

            blk = self.blocks[cur]
            out.extend(clone(s) for s in blk.body)
            t = blk.term

            if isinstance(t, TReturn):
                out.append(A.Return(values=[clone(v) for v in t.values]))
                cur = None
            elif isinstance(t, TRaw):
                out.extend(clone(s) for s in t.stmts)
                cur = None
            elif isinstance(t, TGoto):
                nxt = t.target
                if nxt in self._open_loops:
                    out.append(A.Continue())
                    cur = None
                else:
                    cur = nxt
            elif isinstance(t, TBranch):
                join = self._branch_join(cur, t, stop)
                sset = stop | ({join} if join is not None else set())
                then_b = self._region(t.t, sset)
                else_b = self._region(t.f, sset)
                out.append(self._make_if(clone(t.cond), then_b, else_b))
                cur = join
            else:
                cur = None
        return out

    def _branch_join(self, node: int, t: TBranch, stop: Set[int]) -> Optional[int]:
        """The merge point: earliest (in RPO) node reachable from *both*
        successors without passing back through ``node``."""
        ra = self._reach_from(t.t, block=node)
        rb = self._reach_from(t.f, block=node)
        common = (ra & rb) - {node} - stop
        if not common:
            return None
        rpo = {n: i for i, n in enumerate(self._rpo())}
        return min(common, key=lambda n: rpo.get(n, 1 << 30))

    def _reach_from(self, start: int, block: int) -> Set[int]:
        seen: Set[int] = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in seen or n == block or n not in self.blocks:
                continue
            seen.add(n)
            for s in self.blocks[n].succs():
                stack.append(s)
        return seen

    def _emit_loop(self, header: int, stop: Set[int]) -> Tuple[A.Node, Optional[int]]:
        body_nodes = self.loop_headers[header]
        exits: Set[int] = set()
        for n in body_nodes:
            for s in self.blocks[n].succs():
                if s not in body_nodes:
                    exits.add(s)
        follow = min(exits) if exits else None

        self._open_loops.add(header)
        hb = self.blocks[header]

        sub_stop = stop | {header} | ({follow} if follow is not None else set())

        # while <cond> do ... end   (header is a pure branch: one succ = follow)
        if not hb.body and isinstance(hb.term, TBranch):
            t = hb.term
            if t.f == follow and t.t in body_nodes:
                inner = _strip_trailing_continue(self._region(t.t, sub_stop))
                self._open_loops.discard(header)
                return A.While(cond=clone(t.cond), body=A.Block(stmts=inner)), follow
            if t.t == follow and t.f in body_nodes:
                inner = _strip_trailing_continue(self._region(t.f, sub_stop))
                self._open_loops.discard(header)
                return A.While(cond=A.UnOp(op="not", operand=A.Paren(inner=clone(t.cond))),
                               body=A.Block(stmts=inner)), follow

        # generic: while true do <body> [break] end
        inner = _strip_trailing_continue(
            self._region(header, sub_stop, force_first=True))
        self._open_loops.discard(header)

        # not actually a loop (spurious back-edge / degenerate body): drop it
        real = [s for s in inner if not isinstance(s, (A.Break, A.Continue))]
        if not real:
            return None, follow

        inner = _append_break_on_fallthrough(inner)
        return A.While(cond=A.TrueExpr(), body=A.Block(stmts=inner)), follow

    def _make_if(self, cond: A.Node, then_b: List[A.Node],
                 else_b: List[A.Node]) -> A.Node:
        if then_b and not else_b:
            return A.If(clauses=[(cond, A.Block(stmts=then_b))], orelse=None)
        if else_b and not then_b:
            neg = A.UnOp(op="not", operand=_paren_if_needed(cond))
            return A.If(clauses=[(neg, A.Block(stmts=else_b))], orelse=None)
        return A.If(clauses=[(cond, A.Block(stmts=then_b))],
                    orelse=A.Block(stmts=else_b) if else_b else None)


def _paren_if_needed(cond: A.Node) -> A.Node:
    if isinstance(cond, (A.BinOp,)):
        return A.Paren(inner=cond)
    return cond


def _append_break_on_fallthrough(stmts: List[A.Node]) -> List[A.Node]:
    if stmts and isinstance(stmts[-1], (A.Return, A.Break, A.Continue)):
        return stmts
    return stmts + [A.Break()]


def _strip_trailing_continue(stmts: List[A.Node]) -> List[A.Node]:
    if stmts and isinstance(stmts[-1], A.Continue):
        return stmts[:-1]
    return stmts


def _name_used(stmts, name: str) -> bool:
    for s in stmts:
        for n in A.walk(s):
            if isinstance(n, A.Name) and n.name == name:
                return True
    return False


def _reads_in(node: A.Node) -> Set[str]:
    """All bare-Name reads in ``node`` (LHS bare targets of Assign excluded)."""
    out: Set[str] = set()
    if isinstance(node, A.Assign):
        for t in node.targets:
            t = unparen(t)
            if isinstance(t, A.Index):
                out |= {n.name for n in A.walk(t) if isinstance(n, A.Name)}
        for v in node.values:
            out |= {n.name for n in A.walk(v) if isinstance(n, A.Name)}
        return out
    if isinstance(node, A.LocalAssign):
        for v in node.values:
            out |= {n.name for n in A.walk(v) if isinstance(n, A.Name)}
        return out
    return {n.name for n in A.walk(node) if isinstance(n, A.Name)}


def _prune_dead_stores(stmts: List[A.Node], live_after: Set[str]) -> bool:
    """Backward walk: drop `x = <pure>` when x is not read before its next
    write.  Compound statements are treated opaquely (everything they mention
    stays live), which keeps this conservative around control flow."""
    changed = False
    live = set(live_after)
    # names referenced by any compound stmt anywhere in this list stay live
    for s in stmts:
        if not isinstance(s, (A.Assign, A.LocalAssign, A.CallStat)):
            live |= {n.name for n in A.walk(s) if isinstance(n, A.Name)}

    new_rev: List[A.Node] = []
    for s in reversed(stmts):
        if isinstance(s, A.Assign) and len(s.targets) == 1 \
                and isinstance(s.targets[0], A.Name):
            x = s.targets[0].name
            has_call = _contains_call(s.values[0]) if s.values else False
            if x not in live and not has_call:
                changed = True
                continue                          # dead store -> drop
            live.discard(x)
            live |= _reads_in(s)
            new_rev.append(s)
            continue
        if isinstance(s, (A.If, A.While, A.Repeat)):
            for b in _sub_blocks(s):
                changed |= _prune_dead_stores(b.stmts, set(live))
        live |= _reads_in(s)
        new_rev.append(s)

    new_rev.reverse()
    stmts[:] = new_rev
    return changed


def _sub_blocks(s: A.Node) -> List[A.Block]:
    out = []
    if isinstance(s, A.If):
        out += [b for _c, b in s.clauses]
        if s.orelse is not None:
            out.append(s.orelse)
    elif isinstance(s, (A.While, A.Repeat, A.Do)):
        out.append(s.body)
    return out


# ======================================================================
# pass entry
# ======================================================================
def run(chunk: A.Chunk, ctx) -> A.Chunk:
    mode = str(ctx.options.get("devirt", "on"))
    if mode in ("off", "false", "0", "deflatten"):
        return chunk
    c = find_container(chunk)
    if c is None:
        return chunk
    try:
        lifter = Lifter(chunk, c, ctx)
        new_fn = lifter.lift()
    except Unsupported as exc:
        ctx.note(f"[vm_lift] could not fully lift ({exc}); leaving for de-flattener")
        return chunk
    except Exception as exc:  # noqa: BLE001
        ctx.note(f"[vm_lift] internal error: {exc!r}; leaving for de-flattener")
        return chunk

    # Replace the whole program with the lifted top-level function body.
    chunk.body = new_fn.body
    ctx.bump("vm_lifted")
    ctx.note(f"[vm_lift] lifted Prometheus VM ({lifter.nblocks} blocks) to "
             "structured Lua")
    return chunk
