#!/bin/bash
# Nextcloud 설정 자동 동기화 스크립트
# 실행: /root/nextcloud-config/scripts/sync.sh
# 크론 등록: 0 2 * * * /root/nextcloud-config/scripts/sync.sh >> /var/log/nextcloud-git-sync.log 2>&1

set -e

REPO_DIR="/root/nextcloud-config"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$DATE] 동기화 시작..."

# ──────────────────────────────────────────
# 1. Apache 설정 복사
# ──────────────────────────────────────────
cp /etc/httpd/conf.d/00-nextcloud.conf "$REPO_DIR/apache/"
echo "  ✔ Apache 설정 복사 완료"

# ──────────────────────────────────────────
# 2. SSL 인증서 복사 (개인키 제외)
# ──────────────────────────────────────────
cp /etc/httpd/ssl/nextcloud.crt "$REPO_DIR/ssl/"
echo "  ✔ SSL 인증서 복사 완료"

# ──────────────────────────────────────────
# 3. Nextcloud config.php 복사 + 민감정보 마스킹
# ──────────────────────────────────────────
cat /var/www/html/nextcloud/config/config.php \
  | sed "s/'password' => '.*'/'password' => '### REDACTED ###'/g" \
  | sed "s/'dbpassword' => '.*'/'dbpassword' => '### REDACTED ###'/g" \
  | sed "s/'secret' => '.*'/'secret' => '### REDACTED ###'/g" \
  | sed "s/'passwordsalt' => '.*'/'passwordsalt' => '### REDACTED ###'/g" \
  > "$REPO_DIR/nextcloud/config.php"
echo "  ✔ Nextcloud config 복사 + 마스킹 완료"

# ──────────────────────────────────────────
# 4. Git commit & push
# ──────────────────────────────────────────
cd "$REPO_DIR"
git add -A

if git diff --cached --quiet; then
  echo "  ℹ 변경사항 없음 - 커밋 생략"
else
  COMMIT_MSG="auto: config update $(date '+%Y-%m-%d %H:%M')"
  git commit -m "$COMMIT_MSG"
  git push origin main
  echo "  ✔ GitHub 푸시 완료"
fi

echo "[$DATE] 동기화 완료"
