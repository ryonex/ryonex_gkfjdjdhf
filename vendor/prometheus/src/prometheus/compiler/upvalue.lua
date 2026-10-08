-- This Script is Part of the Prometheus Obfuscator by levno-710
--
-- upvalue.lua
--
-- RyoNex 0.3.0: upvalue storage uses GC-managed "box" tables.
--
-- The previous model (monotonic ids + manual reference counts + newproxy
-- __gc decrements) is unsound on Lua 5.1: finalization order is unspecified,
-- so the entries-proxy of a closure can be finalized before a userdata
-- finalizer that runs the closure reads its upvalues, silently releasing
-- live values (reproducible counterexample in docs/RYONEX_030_PROGRESS.md).
--
-- Box model: every shared variable owns one table box {value}; registers and
-- closure entry lists reference the box itself. A box lives exactly as long
-- as any register or closure entry references it (plain reference
-- semantics), so no manual counting is needed. The entries-proxy keeps a
-- no-op __gc so it remains in the GC's preserved (finalizable) set while
-- user finalizers run; its metatable then keeps the captured boxes
-- reachable regardless of finalization order.

local Ast = require("prometheus.ast");
local Scope = require("prometheus.scope");

return function(Compiler)
    -- No-op: reference counting is gone. Kept assigned so the runtime
    -- wrapper keeps its shape; the proxy still uses it as its __gc marker.
    function Compiler:createUpvaluesGcFunc()
        local scope = Scope:new(self.scope);
        return Ast.FunctionLiteralExpression({}, Ast.Block({}, scope));
    end

    -- Scope exit releases the scope's reference (the register is cleared by
    -- the caller's assignment). The box itself is collected once no closure
    -- entry references it anymore.
    function Compiler:createFreeUpvalueFunc()
        local scope = Scope:new(self.scope);
        local argVar = scope:addVariable();
        return Ast.FunctionLiteralExpression({Ast.VariableExpression(scope, argVar)},
            Ast.Block({}, scope));
    end

    function Compiler:createUpvaluesProxyFunc()
        local scope = Scope:new(self.scope);
        scope:addReferenceToHigherScope(self.scope, self.newproxyVar);

        local entriesVar = scope:addVariable();

        local ifScope = Scope:new(scope);
        local proxyVar = ifScope:addVariable();
        local metatableVar = ifScope:addVariable();
        local elseScope = Scope:new(scope);
        ifScope:addReferenceToHigherScope(self.scope, self.newproxyVar);
        ifScope:addReferenceToHigherScope(self.scope, self.getmetatableVar);
        ifScope:addReferenceToHigherScope(self.scope, self.upvaluesGcFunctionVar);
        ifScope:addReferenceToHigherScope(scope, entriesVar);
        elseScope:addReferenceToHigherScope(self.scope, self.setmetatableVar);
        elseScope:addReferenceToHigherScope(scope, entriesVar);
        elseScope:addReferenceToHigherScope(self.scope, self.upvaluesGcFunctionVar);

        return Ast.FunctionLiteralExpression({Ast.VariableExpression(scope, entriesVar)}, Ast.Block({
            Ast.IfStatement(Ast.VariableExpression(self.scope, self.newproxyVar), Ast.Block({
                Ast.LocalVariableDeclaration(ifScope, {proxyVar}, {
                    Ast.FunctionCallExpression(Ast.VariableExpression(self.scope, self.newproxyVar), {
                        Ast.BooleanExpression(true)
                    });
                });
                Ast.LocalVariableDeclaration(ifScope, {metatableVar}, {
                    Ast.FunctionCallExpression(Ast.VariableExpression(self.scope, self.getmetatableVar), {
                        Ast.VariableExpression(ifScope, proxyVar);
                    });
                });
                Ast.AssignmentStatement({
                    Ast.AssignmentIndexing(Ast.VariableExpression(ifScope, metatableVar), Ast.StringExpression("__index")),
                    Ast.AssignmentIndexing(Ast.VariableExpression(ifScope, metatableVar), Ast.StringExpression("__gc")),
                    Ast.AssignmentIndexing(Ast.VariableExpression(ifScope, metatableVar), Ast.StringExpression("__len")),
                }, {
                    Ast.VariableExpression(scope, entriesVar),
                    Ast.VariableExpression(self.scope, self.upvaluesGcFunctionVar),
                    Ast.FunctionLiteralExpression({}, Ast.Block({
                        Ast.ReturnStatement({Ast.NumberExpression(self.upvalsProxyLenReturn)})
                    }, Scope:new(ifScope)));
                });
                Ast.ReturnStatement({
                    Ast.VariableExpression(ifScope, proxyVar)
                })
            }, ifScope), {}, Ast.Block({
                Ast.ReturnStatement({Ast.FunctionCallExpression(Ast.VariableExpression(self.scope, self.setmetatableVar), {
                    Ast.TableConstructorExpression({}),
                    Ast.TableConstructorExpression({
                        Ast.KeyedTableEntry(Ast.StringExpression("__gc"), Ast.VariableExpression(self.scope, self.upvaluesGcFunctionVar)),
                        Ast.KeyedTableEntry(Ast.StringExpression("__index"), Ast.VariableExpression(scope, entriesVar)),
                        Ast.KeyedTableEntry(Ast.StringExpression("__len"), Ast.FunctionLiteralExpression({}, Ast.Block({
                            Ast.ReturnStatement({Ast.NumberExpression(self.upvalsProxyLenReturn)})
                        }, Scope:new(ifScope)))),
                    })
                })})
            }, elseScope));
        }, scope));
    end

    -- Allocates a fresh box for one shared variable instance.
    function Compiler:createAllocUpvalFunction()
        local scope = Scope:new(self.scope);
        return Ast.FunctionLiteralExpression({}, Ast.Block({
            Ast.ReturnStatement({
                Ast.TableConstructorExpression({})
            })
        }, scope));
    end
end
