-- This Script is Part of the Prometheus Obfuscator by levno-710
--
-- return.lua
--
-- RyoNex 0.3.0: `return f(...)` in tail position emits a trampoline: when f
-- is a VM closure, its frame descriptor {pos, upvalues, proxy} replaces the
-- current dispatch frame and the loop continues in f's entry block — a
-- proper tail call that does not grow the host stack. Plain Lua functions
-- fall back to an ordinary call + return.

local Ast = require("prometheus.ast");
local AstKind = Ast.AstKind;
local Scope = require("prometheus.scope");

return function(self, statement, funcDepth)
    local scope = self.activeBlock.scope;
    local entries = {};
    local regs = {};

    local first = statement.args[1];
    local isTailCall = #statement.args == 1
        and not first.isParenthesizedExpression
        and first.kind == AstKind.FunctionCallExpression;

    if isTailCall then
        local posState = self.registers[self.POS_REGISTER];
        self.registers[self.POS_REGISTER] = true;
        local baseReg = self:compileExpression(first.base, funcDepth, 1)[1];
        local argExprs = {};
        local argEntries = {};
        local argRegs = {};
        for i, argExpr in ipairs(first.args) do
            if i == #first.args and not argExpr.isParenthesizedExpression and (argExpr.kind == AstKind.FunctionCallExpression or argExpr.kind == AstKind.PassSelfFunctionCallExpression or argExpr.kind == AstKind.VarargExpression) then
                local reg = self:compileExpression(argExpr, funcDepth, self.RETURN_ALL)[1];
                local unpacked = Ast.FunctionCallExpression(
                    self:unpack(scope), {self:register(scope, reg)});
                table.insert(argExprs, unpacked);
                table.insert(argEntries, Ast.TableEntry(unpacked));
                table.insert(argRegs, reg);
            else
                local reg = self:compileExpression(argExpr, funcDepth, 1)[1];
                table.insert(argExprs, self:register(scope, reg));
                table.insert(argEntries, Ast.TableEntry(self:register(scope, reg)));
                table.insert(argRegs, reg);
            end
        end

        local metaReg = self:allocRegister(false);
        scope:addReferenceToHigherScope(self.scope, self.closureMetaVar);
        self:addStatement(self:setRegister(scope, metaReg, Ast.IndexExpression(
            Ast.VariableExpression(self.scope, self.closureMetaVar),
            self:register(scope, baseReg))), {metaReg}, {baseReg}, true);

        -- then: replace the frame; the dispatch loop then enters the callee
        local swapScope = Scope:new(scope);
        swapScope:addReferenceToHigherScope(self.containerFuncScope, self.posVar);
        swapScope:addReferenceToHigherScope(self.containerFuncScope, self.argsVar);
        swapScope:addReferenceToHigherScope(self.containerFuncScope, self.currentUpvaluesVar);
        swapScope:addReferenceToHigherScope(self.containerFuncScope, self.detectGcCollectVar);
        local swapStats = {
            Ast.AssignmentStatement({
                Ast.AssignmentVariable(self.containerFuncScope, self.argsVar)
            }, {
                self:pack(swapScope, argEntries)
            });
            Ast.AssignmentStatement({
                Ast.AssignmentVariable(self.containerFuncScope, self.posVar)
            }, {
                Ast.IndexExpression(self:register(swapScope, metaReg), Ast.NumberExpression(1))
            });
            Ast.AssignmentStatement({
                Ast.AssignmentVariable(self.containerFuncScope, self.currentUpvaluesVar)
            }, {
                Ast.IndexExpression(self:register(swapScope, metaReg), Ast.NumberExpression(2))
            });
            Ast.AssignmentStatement({
                Ast.AssignmentVariable(self.containerFuncScope, self.detectGcCollectVar)
            }, {
                Ast.IndexExpression(self:register(swapScope, metaReg), Ast.NumberExpression(3))
            });
        };

        -- else: ordinary call + return
        local callScope = Scope:new(scope);
        callScope:addReferenceToHigherScope(self.containerFuncScope, self.posVar);
        local callStats = {
            self:setReturn(callScope, self:pack(callScope, {
                Ast.TableEntry(Ast.FunctionCallExpression(self:register(callScope, baseReg), argExprs))
            }));
            Ast.AssignmentStatement({
                Ast.AssignmentVariable(self.containerFuncScope, self.posVar)
            }, {
                Ast.NilExpression()
            });
        };

        local cond = Ast.NotEqualsExpression(self:register(scope, metaReg), Ast.NilExpression());
        self:addStatement(Ast.IfStatement(cond, Ast.Block(swapStats, swapScope), {}, Ast.Block(callStats, callScope)),
            {self.POS_REGISTER, self.RETURN_REGISTER}, {metaReg, baseReg, unpack(argRegs)}, false);

        self:freeRegister(metaReg, false);
        self:freeRegister(baseReg, false);
        for _, reg in ipairs(argRegs) do
            self:freeRegister(reg, false);
        end
        self.registers[self.POS_REGISTER] = posState;
        self.activeBlock.advanceToNextBlock = false;
        return;
    end

    for i, expr in ipairs(statement.args) do
        if i == #statement.args and not expr.isParenthesizedExpression and (expr.kind == AstKind.FunctionCallExpression or expr.kind == AstKind.PassSelfFunctionCallExpression or expr.kind == AstKind.VarargExpression) then
            local reg = self:compileExpression(expr, funcDepth, self.RETURN_ALL)[1];
            table.insert(entries, Ast.TableEntry(Ast.FunctionCallExpression(
                self:unpack(scope),
                {self:register(scope, reg)})));
            table.insert(regs, reg);
        else
            local reg = self:compileExpression(expr, funcDepth, 1)[1];
            table.insert(entries, Ast.TableEntry(self:register(scope, reg)));
            table.insert(regs, reg);
        end
    end

    for _, reg in ipairs(regs) do
        self:freeRegister(reg, false);
    end

    self:addStatement(self:setReturn(scope, self:pack(scope, entries)), {self.RETURN_REGISTER}, regs, false);
    self:addStatement(self:setPos(self.activeBlock.scope, nil), {self.POS_REGISTER}, {}, false);
    self.activeBlock.advanceToNextBlock = false;
end;
