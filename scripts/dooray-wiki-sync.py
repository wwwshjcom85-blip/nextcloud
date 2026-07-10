#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
두레이(Dooray) 위키 → Nextcloud 동기화 스크립트 v2
===================================================
두레이 프로젝트의 위키 페이지를 트리 구조 그대로 가져와서
Nextcloud WebDAV를 통해 마크다운 파일로 업로드합니다.

사용법:
  python3 dooray-wiki-sync.py                      # 전체 프로젝트 동기화
  python3 dooray-wiki-sync.py --project "Ahnlab"    # 특정 프로젝트 필터
  python3 dooray-wiki-sync.py --list                # 프로젝트 목록만 조회
  python3 dooray-wiki-sync.py --dry-run             # 실제 업로드 없이 테스트

작성일: 2026-07-10
"""

import json
import os
import sys
import time
import re
import argparse
import logging
import urllib.request
import urllib.error
import urllib.parse
import base64
import ssl
from datetime import datetime

# ──────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────
CONFIG = {
    # 두레이 API
    "dooray_api_token": "u0rw6xeqvnan:SKJbOzRPRnuENjjR8pXtlg",
    "dooray_api_base": "https://api.dooray.com",

    # Nextcloud WebDAV
    "nc_url": "https://127.0.0.1",
    "nc_host": "docs.anyit.net",
    "nc_user": "admin",
    "nc_password": "qhdks00~!",
    "nc_upload_dir": "/Dooray_Wiki",

    # 동기화 옵션
    "sync_images": True,
    "max_depth": 10,            # 최대 트리 깊이
    "rate_limit_delay": 0.2,    # API 호출 간 지연(초)
}

# ──────────────────────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dooray-sync")

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


# ──────────────────────────────────────────────────────────────
# 두레이 API 클라이언트
# ──────────────────────────────────────────────────────────────
class DoorayClient:
    def __init__(self, token, base_url):
        self.token = token
        self.base_url = base_url

    def _request(self, path):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"dooray-api {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
                data = json.load(resp)
                if data.get("header", {}).get("isSuccessful"):
                    return data
        except urllib.error.HTTPError as e:
            logger.error(f"HTTP {e.code}: {url}")
        except Exception as e:
            logger.error(f"요청 실패: {e}")
        return None

    def get_projects(self):
        all_projects = []
        for page in range(10):
            data = self._request(f"/project/v1/projects?page={page}&size=100")
            if not data:
                break
            projects = data.get("result", [])
            if not projects:
                break
            all_projects.extend(projects)
            time.sleep(CONFIG["rate_limit_delay"])
        return all_projects

    def get_wikis(self):
        """전체 위키 목록 (homePageId 포함)"""
        all_wikis = []
        for page in range(10):
            data = self._request(f"/wiki/v1/wikis?page={page}&size=100")
            if not data:
                break
            wikis = data.get("result", [])
            if not wikis:
                break
            all_wikis.extend(wikis)
            time.sleep(CONFIG["rate_limit_delay"])
        return all_wikis

    def get_child_pages(self, wiki_id, parent_page_id):
        """특정 페이지의 하위 페이지 목록 조회"""
        data = self._request(
            f"/wiki/v1/wikis/{wiki_id}/pages?parentPageId={parent_page_id}&page=0&size=200"
        )
        if data:
            return data.get("result", [])
        return []

    def get_page_detail(self, wiki_id, page_id):
        """위키 페이지 상세 내용 조회"""
        data = self._request(f"/wiki/v1/wikis/{wiki_id}/pages/{page_id}")
        if data:
            return data.get("result")
        return None

    def download_image(self, wiki_id, attach_file_id):
        url = f"{self.base_url}/wiki/v1/wikis/{wiki_id}/attachFiles/{attach_file_id}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"dooray-api {self.token}")
        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            logger.error(f"이미지 다운로드 실패 ({attach_file_id}): {e}")
            return None


# ──────────────────────────────────────────────────────────────
# Nextcloud WebDAV 클라이언트 (한글 URL 인코딩 수정)
# ──────────────────────────────────────────────────────────────
class NextcloudClient:
    def __init__(self, url, host, user, password):
        self.base_url = url.rstrip("/")
        self.host = host
        self.user = user
        self.password = password
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.webdav_base = f"{self.base_url}/remote.php/dav/files/{user}"

    def _request(self, path, method="GET", data=None, content_type=None):
        # 한글·특수문자를 URL 퍼센트 인코딩 (슬래시는 유지)
        encoded_path = urllib.parse.quote(path, safe="/")
        url = f"{self.webdav_base}{encoded_path}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Basic {self.auth}")
        req.add_header("Host", self.host)
        req.add_header("OCS-APIRequest", "true")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 405:
                return 405, b""
            return e.code, e.read()
        except Exception as e:
            logger.error(f"Nextcloud 요청 실패: {e}")
            return 0, b""

    def mkdir(self, path):
        status, _ = self._request(path, method="MKCOL")
        return status in (201, 405)

    def mkdir_recursive(self, path):
        parts = path.strip("/").split("/")
        current = ""
        for part in parts:
            current += f"/{part}"
            self.mkdir(current)

    def upload_file(self, path, content, content_type="application/octet-stream"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        status, _ = self._request(path, method="PUT", data=content, content_type=content_type)
        if status in (200, 201, 204):
            return True
        logger.error(f"업로드 실패 ({status}): {path}")
        return False


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────
def sanitize_filename(name):
    """파일/폴더명에 사용 불가한 문자 제거 (한글 유지)"""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    return name[:200] if name else "untitled"


# ──────────────────────────────────────────────────────────────
# 트리 구조 재귀 동기화
# ──────────────────────────────────────────────────────────────
def sync_page_tree(dooray, nc, wiki_id, page_id, nc_dir, depth, dry_run, stats):
    """페이지와 하위 페이지를 재귀적으로 동기화"""
    if depth > CONFIG["max_depth"]:
        return

    # 페이지 상세 조회
    detail = dooray.get_page_detail(wiki_id, page_id)
    if not detail:
        return

    subject = detail.get("subject", "untitled")
    body = detail.get("body", {})
    content = body.get("content", "")
    mime_type = body.get("mimeType", "text/x-markdown")
    images = detail.get("images", [])
    updated_at = detail.get("updatedAt", "")

    safe_subject = sanitize_filename(subject)
    indent = "  " * depth

    # 하위 페이지 조회
    children = dooray.get_child_pages(wiki_id, page_id)
    time.sleep(CONFIG["rate_limit_delay"])

    if children:
        # 하위 페이지가 있으면 폴더 생성 후 index.md로 저장
        page_dir = f"{nc_dir}/{safe_subject}"
        if not dry_run:
            nc.mkdir_recursive(page_dir)

        # 현재 페이지 내용 저장
        if content.strip():
            header = _make_header(subject, wiki_id, page_id, updated_at)
            full_content = header + _process_images(
                content, wiki_id, images, dooray, nc, page_dir, dry_run
            )
            file_path = f"{page_dir}/_index.md" if "markdown" in mime_type else f"{page_dir}/_index.html"

            if dry_run:
                logger.info(f"{indent}📁 {subject}/ (_index + {len(children)}개 하위)")
            else:
                logger.info(f"{indent}📁 {subject}/ ({len(children)}개 하위)")
                nc.upload_file(file_path, full_content, "text/markdown; charset=utf-8")
            stats["pages"] += 1
        else:
            if dry_run:
                logger.info(f"{indent}📁 {subject}/ ({len(children)}개 하위, 빈 본문)")
            else:
                logger.info(f"{indent}📁 {subject}/ ({len(children)}개 하위)")

        # 하위 페이지 재귀 처리
        for child in children:
            child_id = child.get("id")
            sync_page_tree(dooray, nc, wiki_id, child_id, page_dir, depth + 1, dry_run, stats)

    else:
        # 하위 페이지가 없으면 파일로 저장
        if content.strip():
            header = _make_header(subject, wiki_id, page_id, updated_at)
            full_content = header + _process_images(
                content, wiki_id, images, dooray, nc, nc_dir, dry_run
            )
            ext = ".md" if "markdown" in mime_type else ".html"
            file_path = f"{nc_dir}/{safe_subject}{ext}"

            if dry_run:
                logger.info(f"{indent}📄 {subject}")
            else:
                logger.info(f"{indent}📄 {subject}")
                nc.upload_file(file_path, full_content, "text/markdown; charset=utf-8")
            stats["pages"] += 1
        else:
            logger.info(f"{indent}⏭️  {subject} (빈 페이지)")


def _make_header(subject, wiki_id, page_id, updated_at):
    return f"""---
title: "{subject}"
source: Dooray Wiki
wiki_id: "{wiki_id}"
page_id: "{page_id}"
synced_at: "{datetime.now().isoformat()}"
updated_at: "{updated_at}"
---

"""


def _process_images(content, wiki_id, images, dooray, nc, nc_dir, dry_run):
    """본문 내 이미지 참조 치환 및 다운로드"""
    if not CONFIG["sync_images"] or not images:
        return content

    for img in images:
        attach_id = img.get("attachFileId", "")
        img_name = sanitize_filename(img.get("name", f"img_{attach_id}"))

        # 프로젝트 ID 또는 위키 ID가 달라서 치환이 안 되는 문제 해결 (정규식 사용)
        old_ref_pattern = rf"/wikis/\d+/files/{attach_id}"
        content = re.sub(old_ref_pattern, f"images/{img_name}", content)

        if not dry_run:
            img_dir = f"{nc_dir}/images"
            nc.mkdir(img_dir)
            img_data = dooray.download_image(wiki_id, attach_id)
            if img_data:
                nc.upload_file(f"{img_dir}/{img_name}", img_data)
            time.sleep(CONFIG["rate_limit_delay"])

    return content


# ──────────────────────────────────────────────────────────────
# 위키별 동기화
# ──────────────────────────────────────────────────────────────
def sync_wiki(dooray, nc, wiki_info, dry_run=False):
    """하나의 위키를 전체 트리 구조로 동기화"""
    wiki_id = wiki_info["id"]
    wiki_name = wiki_info.get("name", "unknown")
    home_page_id = wiki_info.get("home", {}).get("pageId")

    if not home_page_id:
        logger.info(f"  ⏭️  홈 페이지 없음: {wiki_name}")
        return {"pages": 0}

    safe_name = sanitize_filename(wiki_name)
    nc_wiki_dir = f"{CONFIG['nc_upload_dir']}/{safe_name}"

    logger.info(f"  📂 위키: {wiki_name} (wikiId={wiki_id})")

    if not dry_run:
        nc.mkdir_recursive(nc_wiki_dir)

    stats = {"pages": 0}

    # 홈 페이지의 하위 페이지부터 시작 (홈 페이지 자체도 저장)
    # 홈 페이지 상세 조회
    home_detail = dooray.get_page_detail(wiki_id, home_page_id)
    if home_detail:
        body = home_detail.get("body", {})
        content = body.get("content", "")
        images = home_detail.get("images", [])
        if content.strip():
            header = _make_header("Home", wiki_id, home_page_id, home_detail.get("updatedAt", ""))
            full_content = header + _process_images(
                content, wiki_id, images, dooray, nc, nc_wiki_dir, dry_run
            )
            if not dry_run:
                nc.upload_file(f"{nc_wiki_dir}/_index.md", full_content, "text/markdown; charset=utf-8")
            stats["pages"] += 1

    # 하위 페이지 트리 탐색
    children = dooray.get_child_pages(wiki_id, home_page_id)
    time.sleep(CONFIG["rate_limit_delay"])

    if children:
        logger.info(f"    → {len(children)}개 하위 페이지 발견")
        for child in children:
            child_id = child.get("id")
            sync_page_tree(dooray, nc, wiki_id, child_id, nc_wiki_dir, 2, dry_run, stats)
    else:
        logger.info(f"    → 하위 페이지 없음 (홈 페이지만 존재)")

    return stats


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="두레이 위키 → Nextcloud 동기화")
    parser.add_argument("--list", action="store_true", help="위키 목록만 조회")
    parser.add_argument("--project", type=str, help="위키명 필터 (부분 일치)")
    parser.add_argument("--wiki-id", type=str, help="특정 위키 ID만 동기화")
    parser.add_argument("--dry-run", action="store_true", help="업로드 없이 테스트")
    parser.add_argument("--no-images", action="store_true", help="이미지 비활성화")
    args = parser.parse_args()

    if args.no_images:
        CONFIG["sync_images"] = False

    logger.info("=" * 60)
    logger.info("🔄 두레이 위키 → Nextcloud 동기화 v2")
    logger.info("=" * 60)

    dooray = DoorayClient(CONFIG["dooray_api_token"], CONFIG["dooray_api_base"])
    nc = NextcloudClient(CONFIG["nc_url"], CONFIG["nc_host"], CONFIG["nc_user"], CONFIG["nc_password"])

    # 위키 목록 조회
    logger.info("📋 위키 목록 조회 중...")
    wikis = dooray.get_wikis()
    logger.info(f"   총 {len(wikis)}개 위키 발견")

    if args.list:
        print(f"\n{'#':>4}  {'위키명':<40} {'위키ID':<22} {'타입'}")
        print("-" * 90)
        for i, w in enumerate(wikis, 1):
            print(f"{i:>4}. {w.get('name',''):<40} {w['id']:<22} {w.get('type','')}")
        return

    # 필터링
    if args.project:
        wikis = [w for w in wikis if args.project in w.get("name", "")]
        logger.info(f"   '{args.project}' 필터 → {len(wikis)}개 일치")
    elif args.wiki_id:
        wikis = [w for w in wikis if w["id"] == args.wiki_id]

    if not wikis:
        logger.warning("동기화할 위키가 없습니다.")
        return

    # Nextcloud 루트 디렉토리 생성
    if not args.dry_run:
        nc.mkdir_recursive(CONFIG["nc_upload_dir"])

    # 동기화 실행
    total_pages = 0
    total_wikis = 0

    for wiki in wikis:
        try:
            stats = sync_wiki(dooray, nc, wiki, dry_run=args.dry_run)
            if stats["pages"] > 0:
                total_pages += stats["pages"]
                total_wikis += 1
        except Exception as e:
            logger.error(f"  ❌ 위키 동기화 실패: {wiki.get('name','')} - {e}")

    # 결과 출력
    logger.info("=" * 60)
    logger.info("📊 동기화 완료")
    logger.info(f"   위키: {total_wikis}개 처리")
    logger.info(f"   페이지: {total_pages}개 동기화")
    if args.dry_run:
        logger.info("   ⚠️  DRY-RUN 모드: 실제 업로드 없음")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
