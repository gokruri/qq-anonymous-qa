import re, sys
src = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r"Ee=\{\s*\d+:", src)
seg = src[m.start():m.start() + 30000]
pairs = re.findall(r'(\d+):"([^"]*)"', seg)
def unesc(s):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), s)
print("pairs=%d" % len(pairs))
for k, v in pairs:
    if int(k) < 200:
        print("  %-6s %s" % (k, unesc(v)))
