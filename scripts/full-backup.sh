#!/bin/bash
# ============================================================
# Nextcloud 서버 완전 복구용 종합 백업 스크립트
# 서버: docs.anyit.net | Rocky Linux 9.6
# GitHub: git@github.com:wwwshjcom85-blip/nextcloud.git
# ============================================================
set -e

REPO_DIR="/root/nextcloud-config"
LOG_FILE="/var/log/nextcloud-git-sync.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')
CHANGED=0

log() { echo "[$DATE] $1" | tee -a "$LOG_FILE"; }

log "========================================"
log "전체 백업 시작"
log "========================================"

# ──────────────────────────────────────────
# [1] Apache 설정
# ──────────────────────────────────────────
log "[1/8] Apache 설정 백업..."
cp /etc/httpd/conf/httpd.conf                 "$REPO_DIR/apache/"
cp /etc/httpd/conf.d/00-nextcloud.conf        "$REPO_DIR/apache/"
cp /etc/httpd/conf.d/php.conf                 "$REPO_DIR/apache/" 2>/dev/null || true
# 전체 conf.d 목록도 기록
ls -la /etc/httpd/conf.d/ > "$REPO_DIR/apache/conf.d_filelist.txt"
ls -la /etc/httpd/conf.modules.d/ > "$REPO_DIR/apache/modules_filelist.txt"
log "  ✔ Apache 설정 완료"

# ──────────────────────────────────────────
# [2] PHP 설정
# ──────────────────────────────────────────
log "[2/8] PHP 설정 백업..."
PHP_INI=$(php --ini 2>/dev/null | grep "Loaded Configuration" | awk '{print $NF}')
[ -f "$PHP_INI" ] && cp "$PHP_INI" "$REPO_DIR/php/php.ini"
cp /etc/php-fpm.d/www.conf "$REPO_DIR/php/www.conf"
# php.d 설정 목록
ls /etc/php.d/ > "$REPO_DIR/php/php.d_modules.txt"
php -m > "$REPO_DIR/php/php_modules.txt"
log "  ✔ PHP 설정 완료"

# ──────────────────────────────────────────
# [3] Nextcloud config.php (민감정보 마스킹본 + 암호화 실제본)
# ──────────────────────────────────────────
log "[3/8] Nextcloud config 백업..."

# 마스킹 버전 (GitHub 공개용)
cat /var/www/html/nextcloud/config/config.php \
  | sed "s/'dbpassword' => '.*'/'dbpassword' => '### REDACTED ###'/g" \
  | sed "s/'secret' => '.*'/'secret' => '### REDACTED ###'/g" \
  | sed "s/'passwordsalt' => '.*'/'passwordsalt' => '### REDACTED ###'/g" \
  > "$REPO_DIR/nextcloud/config.php.masked"

# 실제 config.php → AES-256-CBC 암호화 (복호화: openssl enc -d -aes-256-cbc -in config.php.enc -out config.php -k NEXTCLOUD_BACKUP_KEY)
BACKUP_KEY=${NEXTCLOUD_BACKUP_KEY:-"anyit-nextcloud-2026"}
openssl enc -aes-256-cbc -pbkdf2 -in /var/www/html/nextcloud/config/config.php \
  -out "$REPO_DIR/nextcloud/config.php.enc" -k "$BACKUP_KEY"

# Nextcloud 버전 정보
php /var/www/html/nextcloud/occ status 2>/dev/null > "$REPO_DIR/nextcloud/nc_status.txt" || \
  grep "'version'" /var/www/html/nextcloud/config/config.php > "$REPO_DIR/nextcloud/nc_status.txt"

# 활성화된 앱 목록
php /var/www/html/nextcloud/occ app:list 2>/dev/null > "$REPO_DIR/nextcloud/app_list.txt" || true

# themes 디렉토리
rsync -a --delete /var/www/html/nextcloud/themes/ "$REPO_DIR/nextcloud/themes/" 2>/dev/null || true

log "  ✔ Nextcloud config 완료"

# ──────────────────────────────────────────
# [4] SSL 인증서 + 개인키 (개인키 암호화)
# ──────────────────────────────────────────
log "[4/8] SSL 인증서 백업..."

cp /etc/httpd/ssl/nextcloud.crt "$REPO_DIR/ssl/"

# 개인키 암호화 저장 (복호화: openssl enc -d -aes-256-cbc -in nextcloud.key.enc -out nextcloud.key -k NEXTCLOUD_BACKUP_KEY)
openssl enc -aes-256-cbc -pbkdf2 -in /etc/httpd/ssl/nextcloud.key \
  -out "$REPO_DIR/ssl/nextcloud.key.enc" -k "$BACKUP_KEY"

# 인증서 만료일 기록
openssl x509 -in /etc/httpd/ssl/nextcloud.crt -noout -dates > "$REPO_DIR/ssl/cert_expiry.txt" 2>/dev/null || true

log "  ✔ SSL 인증서 완료"

# ──────────────────────────────────────────
# [5] MariaDB 데이터베이스 덤프
# ──────────────────────────────────────────
log "[5/8] MariaDB 덤프 백업..."

DB_PASS=$(grep -oP "'dbpassword' => '\K[^']*" /var/www/html/nextcloud/config/config.php | head -1)
DB_USER=$(grep -oP "'dbuser' => '\K[^']*" /var/www/html/nextcloud/config/config.php | head -1)
DB_NAME=$(grep -oP "'dbname' => '\K[^']*" /var/www/html/nextcloud/config/config.php | head -1)

mysqldump -u"$DB_USER" -p"$DB_PASS" \
  --single-transaction --quick --lock-tables=false \
  "$DB_NAME" 2>/dev/null | gzip > "$REPO_DIR/db/nextcloud_db.sql.gz"

# DB 크기 기록
du -sh "$REPO_DIR/db/nextcloud_db.sql.gz" > "$REPO_DIR/db/db_size.txt"
echo "Backup time: $DATE" >> "$REPO_DIR/db/db_size.txt"

log "  ✔ DB 덤프 완료 ($(cat $REPO_DIR/db/db_size.txt | head -1))"

# ──────────────────────────────────────────
# [6] Docker / OnlyOffice 설정
# ──────────────────────────────────────────
log "[6/8] Docker/OnlyOffice 설정 백업..."

# 실행 중인 컨테이너 정보
docker ps -a --format "table {{.ID}}\t{{.Image}}\t{{.Command}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}" \
  > "$REPO_DIR/docker/containers.txt" 2>/dev/null || true

# OnlyOffice 컨테이너 상세 설정
docker inspect onlyoffice-ds > "$REPO_DIR/docker/onlyoffice-ds_inspect.json" 2>/dev/null || true

# docker-compose 파일이 있으면 복사
find /root /opt /srv -name "docker-compose*.yml" -o -name "docker-compose*.yaml" 2>/dev/null | \
  while read f; do cp "$f" "$REPO_DIR/docker/$(basename $f)"; done || true

# OnlyOffice 재시작 명령 기록
echo "docker run -d --name onlyoffice-ds --restart=unless-stopped -p 8080:80 onlyoffice/documentserver" \
  > "$REPO_DIR/docker/onlyoffice_run_command.sh"

log "  ✔ Docker 설정 완료"

# ──────────────────────────────────────────
# [7] 시스템 정보 (방화벽, crontab, 서비스, 패키지)
# ──────────────────────────────────────────
log "[7/8] 시스템 정보 백업..."

# 방화벽 규칙
firewall-cmd --list-all > "$REPO_DIR/system/firewall_rules.txt" 2>/dev/null || true
firewall-cmd --list-all-zones > "$REPO_DIR/system/firewall_zones.txt" 2>/dev/null || true

# 현재 crontab
crontab -l > "$REPO_DIR/system/crontab_root.txt" 2>/dev/null || echo "no crontab" > "$REPO_DIR/system/crontab_root.txt"

# 실행 중인 서비스 목록
systemctl list-units --type=service --state=running --no-pager > "$REPO_DIR/system/running_services.txt"

# 활성화된 서비스 목록 (부팅 자동시작)
systemctl list-unit-files --state=enabled --no-pager > "$REPO_DIR/system/enabled_services.txt"

# 설치된 패키지 목록
rpm -qa --queryformat "%{NAME}-%{VERSION}\n" | sort > "$REPO_DIR/system/installed_packages.txt"

# 네트워크 포트 목록
ss -tlnp > "$REPO_DIR/system/open_ports.txt" 2>/dev/null || true

# 시스템 정보
uname -a > "$REPO_DIR/system/system_info.txt"
cat /etc/os-release >> "$REPO_DIR/system/system_info.txt"
df -h >> "$REPO_DIR/system/system_info.txt"
free -h >> "$REPO_DIR/system/system_info.txt"

# /etc/hosts
cp /etc/hosts "$REPO_DIR/system/hosts"

log "  ✔ 시스템 정보 완료"

# ──────────────────────────────────────────
# [8] Git commit & push
# ──────────────────────────────────────────
log "[8/8] GitHub 동기화..."

cd "$REPO_DIR"
git add -A

if git diff --cached --quiet; then
  log "  ℹ 변경사항 없음 - 커밋 생략"
else
  COMMIT_MSG="backup: $(date '+%Y-%m-%d %H:%M') - auto full backup"
  git commit -m "$COMMIT_MSG"
  git push origin main
  log "  ✔ GitHub 푸시 완료"
  CHANGED=1
fi

log "========================================"
log "전체 백업 완료 (변경: $CHANGED)"
log "========================================"
