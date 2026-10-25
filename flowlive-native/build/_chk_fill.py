import importlib.util as ilu, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec); sys.modules["native_app"] = NA; spec.loader.exec_module(NA)
NA.ensure_mpl()
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
win = NA.MainWindow(); win.resize(1480, 940); win.show(); app.processEvents()
print("t_in rows BEFORE:", win.t_in.rowCount())
win.data = {"board":[{"name":"A","net":1.0,"d5":0.5,"salience":9}],
            "board_out":[{"name":"B","net":-1.0,"d5":-0.5,"salience":8}],
            "industry":{"in":[{"name":"I1","net":2.0,"d5":0.1,"salience":7}],
                        "out":[{"name":"I2","net":-2.0,"d5":-0.1,"salience":6}]},
            "events":[], "themes":[], "market":{}}
win.fill_all()
app.processEvents()
print("t_in rows AFTER :", win.t_in.rowCount())
print("t_in cell(0,1)  :", win.t_in.item(0,1).text() if win.t_in.item(0,1) else None)
print("t_ind_in rows   :", win.t_ind_in.rowCount())
