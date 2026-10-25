import struct, sys, re, hashlib

p = r"D:\exe\资金流向原生版.exe"
d = open(p, "rb").read()
print("path :", p)
print("size :", len(d))
print("md5  :", hashlib.md5(d).hexdigest())
if d[:2] != b"MZ":
    print("not MZ"); sys.exit()
pe_off = struct.unpack_from("<I", d, 0x3C)[0]
print("PE sig:", d[pe_off:pe_off + 4])

print("--- VS_VERSIONINFO keys (UTF-16LE) ---")
for key in ["FileVersion", "ProductVersion", "FileDescription", "CompanyName",
            "ProductName", "OriginalFilename", "LegalCopyright", "InternalName"]:
    print("  %-18s found=%d" % (key, d.find(key.encode("utf-16-le"))))

print("--- UTF-16 x.y.z strings ---")
u16 = set()
for m in re.finditer(rb"(?:[0-9]\x00\.\x00){2}[0-9]\x00", d):
    try:
        u16.add(m.group().decode("utf-16-le"))
    except Exception:
        pass
print("  ", sorted(u16)[:30])

print("--- ASCII 1.x/2.x strings ---")
a = set(re.findall(rb"\b[12]\.[0-9]\.[0-9]+\b", d))
print("  ", sorted(x.decode() for x in a))
