# Python 3.9 베이스 이미지 사용
FROM python:3.9

# 시스템 패키지 설치
RUN apt-get update && \
    apt-get install -y git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Git 설정
RUN git config --global user.email "rylynn1029@naver.com" && \
    git config --global user.name "jaerius"

# 작업 디렉토리 설정
WORKDIR /app

# 의존성 파일 복사 및 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# 애플리케이션 코드 복사
COPY . .

# Python 패키지 경로에 현재 디렉토리 추가
ENV PYTHONPATH=/app

# 포트 노출
EXPOSE 9000


# 헬스체크 추가
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1


# 실행
CMD ["python", "-m", "api.app"]

