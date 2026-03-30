FROM python:3.12-slim

# 1. TA-Lib C 라이브러리 설치 (.deb 파일 활용)
# wget 설치 -> .deb 다운로드 및 설치 -> wget 삭제 (이미지 경량화)
RUN apt-get update && apt-get install -y --no-install-recommends wget && \
    wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib_0.6.4_amd64.deb && \
    dpkg -i ta-lib_0.6.4_amd64.deb && \
    rm ta-lib_0.6.4_amd64.deb && \
    apt-get purge -y wget && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# 2. uv (고속 파이썬 패키지 매니저) 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 3. 작업 디렉토리 설정 및 의존성 설치 (Layer Caching 활용)
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# 4. 소스 코드 복사
COPY . .

# 5. 애플리케이션 실행
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
