#!/usr/bin/env bash
# 数据备份：SQLite 在线快照（WAL 安全）+ 静态数据打包，按份数轮转
#
# 用法:
#   bash scripts/backup_data.sh [备份目录]        # 默认 data/backups
#   bash scripts/backup_data.sh --neo4j           # 仅打印 Neo4j 备份指引
#
# 可选环境变量:
#   AIGC_BACKUP_KEEP  保留份数（默认 7）
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${1:-${PROJECT_ROOT}/data/backups}"
KEEP="${AIGC_BACKUP_KEEP:-7}"

if [[ "${1:-}" == "--neo4j" ]]; then
  cat <<'EOF'
Neo4j 备份（Community 版无 online dump，需停库）:
  neo4j-admin database dump neo4j --to-stdout | gzip > neo4j_$(date +%Y%m%d_%H%M).dump.gz
恢复:
  gunzip -c neo4j_xxx.dump.gz | neo4j-admin database load neo4j --from-stdin
建议在低峰期执行；或直接对 Neo4j data/ 目录做文件级快照（同样需停库）。
EOF
  exit 0
fi

command -v sqlite3 >/dev/null || { echo "错误：需要 sqlite3 命令（apt install sqlite3 / brew install sqlite）" >&2; exit 1; }

STAMP="$(date +%Y%m%d_%H%M%S)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
SNAP_DIR="$WORK_DIR/snapshot"
mkdir -p "$SNAP_DIR"

# 1. SQLite 在线快照（.backup 走备份 API，对 WAL 库安全；不要在运行时裸拷 db 文件）
for db in data/memory/graphrag_checkpoints.db data/memory/mastery.db; do
  src="$PROJECT_ROOT/$db"
  if [[ -f "$src" ]]; then
    sqlite3 "$src" ".backup '$SNAP_DIR/$(basename "$db")'"
    echo "已快照 $db"
  else
    echo "跳过（不存在）: $db"
  fi
done

# 2. 静态数据（题库 JSON / 图谱源数据 / 试卷 / quiz 存储）
cd "$PROJECT_ROOT"
rsync -a --relative data/layers data/exam_papers data/mcq_*.json data/quiz "$SNAP_DIR/" 2>/dev/null || {
  # 无 rsync 时退回 tar 直取
  tar -cf - data/layers data/exam_papers data/mcq_*.json data/quiz 2>/dev/null | tar -xf - -C "$SNAP_DIR"
}

# 3. 打包
mkdir -p "$BACKUP_DIR"
ARCHIVE="$BACKUP_DIR/aigc_backup_${STAMP}.tar.gz"
tar -czf "$ARCHIVE" -C "$SNAP_DIR" .
echo "已生成 $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# 4. 轮转：只保留最近 KEEP 份
ls -1t "$BACKUP_DIR"/aigc_backup_*.tar.gz 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
  echo "已轮转删除 $old"
done

echo "完成，当前保留 $(ls -1 "$BACKUP_DIR"/aigc_backup_*.tar.gz 2>/dev/null | wc -l | tr -d ' ') 份备份。"
