import re, sys
fn = sys.argv[1]
src = open(fn, encoding="utf-8", errors="replace").read()

def unesc(s):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), s)

# 找所有 变量={ 数字:"非空" ...} 形式的小表
seen = set()
for m in re.finditer(r'([A-Za-z_$][\w$]*)=\{(\d{1,4}:"[^"]{2,400}")', src):
    name = m.group(1)
    start = m.start(2)
    seg = src[start:start + 20000]
    pairs = re.findall(r'(\d+):"([^"]*)"', seg)
    if len(pairs) < 2:
        continue
    nums = [int(k) for k, _ in pairs]
    if max(nums) > 5000:
        continue
    key = (name, tuple(sorted(nums))[:20])
    if key in seen:
        continue
    seen.add(key)
    print("=" * 88)
    print("### 变量 %s  (%d 条) @%d" % (name, len(pairs), m.start()))
    for k, v in pairs:
        print("   %-6s %s" % (k, unesc(v)))
