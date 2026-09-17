#!/bin/bash
# 拿一个工具链 (docs/177 P2)。用法: loment_toolchain.sh zig|lua|jdk
# **放在 $HOME**: WSL 闲置会关停, /tmp 是 tmpfs 一关就清空。
set -u
OUT="$HOME/fujotc"
mkdir -p "$OUT"
CURL="curl -L --retry 4 --retry-delay 2 --retry-all-errors --max-time 900 -o"

case "${1:-}" in
zig)
  cd "$OUT" || exit 1
  if [ -x "$OUT/zig/zig" ]; then echo "zig 已有: $("$OUT/zig/zig" version)"; exit 0; fi
  URL=$(curl -sL --max-time 120 https://ziglang.org/download/index.json | python3 -c '
import json,sys
d=json.load(sys.stdin)
def key(v):
    try: return tuple(int(x) for x in v.split("."))
    except Exception: return None
c=[(key(k),k) for k in d if key(k)]
print(d[max(c)[1]].get("x86_64-linux",{}).get("tarball","") if c else "")
')
  echo "URL=$URL"
  [ -z "$URL" ] && { echo "拿不到 URL"; exit 1; }
  rm -f zig.tar.xz
  $CURL zig.tar.xz "$URL" && rm -rf zig && mkdir zig \
    && tar xf zig.tar.xz -C zig --strip-components=1 \
    && echo "zig: $("$OUT/zig/zig" version)" || echo "zig 失败"
  ;;
lua)
  cd "$OUT" || exit 1
  if [ -x "$OUT/lua-5.4.7/src/lua" ]; then echo "lua 已有"; exit 0; fi
  $CURL lua.tar.gz https://www.lua.org/ftp/lua-5.4.7.tar.gz \
    && tar xf lua.tar.gz \
    && (cd lua-5.4.7 && make linux -j8 >"$OUT/lua-make.log" 2>&1)
  if [ -x "$OUT/lua-5.4.7/src/lua" ]; then
    echo "lua: $("$OUT/lua-5.4.7/src/lua" -v 2>&1)"
  else
    echo "lua 失败"; tail -6 "$OUT/lua-make.log" 2>/dev/null
  fi
  ;;
jdk)
  cd "$OUT" || exit 1
  if [ -x "$OUT/jdk/bin/java" ]; then echo "jdk 已有"; exit 0; fi
  rm -f jdk.tar.gz
  $CURL jdk.tar.gz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse" \
    && rm -rf jdk && mkdir jdk && tar xf jdk.tar.gz -C jdk --strip-components=1
  if [ -x "$OUT/jdk/bin/java" ]; then
    echo "java: $("$OUT/jdk/bin/java" -version 2>&1 | head -1)"
  else
    echo "jdk 失败 (size=$(stat -c%s jdk.tar.gz 2>/dev/null || echo 0))"
  fi
  ;;
*)
  echo "用法: _tc.sh zig|lua|jdk"; exit 2;;
esac
