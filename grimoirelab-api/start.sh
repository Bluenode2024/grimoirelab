#!/bin/bash

# SSH 키 권한 설정
if [ -f /root/.ssh/id_rsa ]; then
    chmod 600 /root/.ssh/id_rsa
    # GitHub의 호스트 키 추가
    ssh-keyscan github.com >> /root/.ssh/known_hosts
fi

# API 서버 실행
exec python -m api.app 