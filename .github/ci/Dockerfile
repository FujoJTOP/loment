# 门禁的 CI 工具链 —— **只在这里装一次**，各 job 用 `container:` 拉它。
#
# ## 为什么要它（docs/213 §7）
#
# 装工具链那 95s（apt 55s + LLVM 19 35s）原来是**每个分片各付一次** —— 而它是"同一份
# 东西装 N 遍"。做成镜像之后，那 95s 只在**镜像构建时**付一次（且只在下面这份文件改动
# 时），各分片只剩拉镜像。顺带把"apt 在镜像上抽风"的暴露面也一起消掉：抽风的那一次
# 发生在构建里，不占任何人的墙钟。
#
# ## 包名单从哪来
#
# **不是新设计的**，是原样搬 `gate.yml` 里那两条（apt + `llvm.sh`），再加上原来**由
# runner 镜像提供、而容器里没有**的那几件（`git` / `python3` / `gh` / `make` / 解压工具）。
# 那第二类的依据是**逐条扫判据**：`grep -rn 'which("' tools/*.py` 给出各判据"缺了就跳过"
# 的命令名，与这份名单对照。
#
# **不许随手增删**：少一个包就是一条**假红**，多一个包是白等的下载。两条纪律：
#   * **不加** `rustc` / `dotnet` / `nvim` —— runner 上本来就没有，几条判据靠"缺"来
#     SKIP，而**那些 SKIP 已经写进基线**（例如 `loment_multisyntax_projects_test` 那条
#     "runner 上 2/3"）。加进来会改变覆盖面，不是改进。
#   * 增删之后要跑一遍门禁，看 `环境自述` 那一步的输出与红项有没有变。
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# 这一组与原来 `gate.yml` 那一步**逐字对应**（`qemu-system-x86 qemu-user golang-go
# default-jdk nodejs npm vim`），后面几件是 runner 镜像本来就有、容器里得自己装的。
# 不用 `--no-install-recommends`：原来那一步也没用，换掉会静默改环境。
RUN apt-get update -qq && apt-get install -y -qq \
      ca-certificates curl wget gnupg git sudo \
      python3 make unzip xz-utils diffutils \
      qemu-system-x86 qemu-user \
      golang-go default-jdk \
      nodejs npm vim \
    && rm -rf /var/lib/apt/lists/*

# `gh`：`loment_publish` 用它（runner 镜像自带，容器里没有）。
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && wget -qO /etc/apt/keyrings/githubcli-archive-keyring.gpg \
         https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update -qq && apt-get install -y -qq gh \
    && rm -rf /var/lib/apt/lists/*

# gdb：`diagnose_twin` 那个定向探针用它（原来是探针步骤里现装）。
RUN apt-get update -qq && apt-get install -y -qq gdb && rm -rf /var/lib/apt/lists/*

# Node 20（**固定版本**，官方 tarball）盖在 apt 那个 18 上面（`/usr/local/bin` 在
# `/usr/bin` 前面）。两条理由：
#   * **runner 上就是 20**（GitHub 的镜像自带），而 ubuntu 24.04 的 `nodejs` 是 18 ——
#     判据 `vscode_ext_test` 拿它跑 JS，版本对齐更贴近原来的环境；
#   * **`container:` job 里的 JS action**（`actions/checkout`、`upload-artifact@v4` 都是）
#     `using: node20`。它们用的是 runner 挂进来的 node 还是镜像里的，取决于 runner 怎么
#     挂载 —— 猜不如给一个 20 放在 PATH 最前面。
# 用固定 tarball 而不是"下载一个 setup 脚本再执行"：前者可复现（`llvm.sh` 那一步是
# apt.llvm.org 官方给的唯一路子，两回事）。
RUN arch=$(dpkg --print-architecture); \
    case "$arch" in amd64) na=x64 ;; arm64) na=arm64 ;; *) echo "不认识的架构 $arch" >&2; exit 1 ;; esac; \
    wget -qO /tmp/node.tar.xz "https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-$na.tar.xz" \
    && tar -xJf /tmp/node.tar.xz -C /opt \
    && mv "/opt/node-v20.19.0-linux-$na" /opt/node \
    && for b in node npm npx; do ln -sf "/opt/node/bin/$b" "/usr/local/bin/$b"; done \
    && rm -f /tmp/node.tar.xz \
    && /usr/local/bin/node -v

# LLVM 19 + 不带版本号的符号链接 —— 与原来 `gate.yml` 里那几行**同一套**。
# 为什么非要 19：仓里发出的 IR 用 `#dbg_declare`（LLVM 19 起的调试记录语法），而 ubuntu
# 自带的 clang 是 18 → `--debug` 那几条判据会报 `error: expected instruction opcode`。
RUN wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh \
    && chmod +x /tmp/llvm.sh && /tmp/llvm.sh 19 \
    && for t in clang clang++ lld ld.lld llvm-objdump llvm-nm llvm-ar; do \
         if [ -x "/usr/lib/llvm-19/bin/$t" ]; then ln -sf "/usr/lib/llvm-19/bin/$t" "/usr/local/bin/$t"; fi; \
       done \
    && rm -f /tmp/llvm.sh && rm -rf /var/lib/apt/lists/*

# 自述一遍 —— 构建日志里就能看出这一版镜像装成了什么（门禁那边 `环境自述` 是另一半）。
RUN set -e; \
    python3 -V; git --version; make --version | head -1; \
    clang --version | head -1; llvm-objdump --version | head -1; \
    qemu-system-x86_64 --version | head -1; qemu-aarch64 --version | head -1; \
    go version; javac -version; node -v; gh --version | head -1; gdb --version | head -1
