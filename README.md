# Nextcloud Server Configuration Backup

> **서버**: docs.anyit.net  
> **OS**: Rocky Linux 9.6  
> **Nextcloud**: v33.0.0.16  

## 📁 디렉토리 구조

```
nextcloud-config/
├── apache/
│   └── 00-nextcloud.conf       # Apache VirtualHost + OnlyOffice Proxy 설정
├── nextcloud/
│   └── config.php              # Nextcloud 설정 (민감정보 마스킹됨)
├── ssl/
│   └── nextcloud.crt           # SSL 인증서 (개인키 제외)
└── scripts/
    └── sync.sh                 # 자동 동기화 스크립트
```

## ⚠️ 주의사항

- `config.php`의 비밀번호/시크릿 값은 `### REDACTED ###`로 마스킹됨
- SSL 개인키(`.key`)는 보안상 저장소에 포함되지 않음
- 실제 운영 복원 시 마스킹된 값을 수동으로 교체 필요

## 🔄 자동 동기화

매일 새벽 2시 자동 커밋 (크론 등록 후):

```bash
# crontab -e 에 추가
0 2 * * * /root/nextcloud-config/scripts/sync.sh >> /var/log/nextcloud-git-sync.log 2>&1
```

## 🔧 수동 동기화

```bash
/root/nextcloud-config/scripts/sync.sh
```
