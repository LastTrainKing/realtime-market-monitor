import symtable, builtins
p = "app_main_v203.py"
src = open(p, encoding="utf-8").read()
st = symtable.symtable(src, p, "exec")
mod = {s.get_name() for s in st.get_symbols()}
bi = set(dir(builtins))
KNOWN_DYN = {"FanChart","IndexChart","FlowBarChart","ThemeBoard","LeaderGrid","StageGrid","MplCanvas"}
bad = []
def walk(t, path):
    for s in t.get_symbols():
        n = s.get_name()
        if s.is_referenced() and s.is_global() and n not in mod and n not in bi and n not in KNOWN_DYN:
            bad.append((path, n))
    for c in t.get_children():
        walk(c, path + "." + c.get_name())
walk(st, "<module>")
if bad:
    for p_, n in bad: print("FAIL", p_, "->", n)
else:
    print("SCOPE GUARD PASS: 无未定义的全局名引用")
