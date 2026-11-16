FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /publisher
RUN pip install --no-cache-dir flask==3.1.1 waitress==3.0.2 patchright==1.58.2 loguru==0.7.3 opencv-python-headless==4.13.0.92 segno==1.6.6 qrcode==8.2 requests==2.32.3 \
    && python -m patchright install --with-deps chromium
COPY vendor /publisher/vendor
COPY conf.py /publisher/vendor/conf.py
COPY service.py runner.py state.py xiaohongshu.py /publisher/
ENV PYTHONPATH=/publisher/vendor:/publisher
RUN mkdir -p /state /publisher/vendor/logs /publisher/vendor/cookies
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health')"
CMD ["waitress-serve", "--listen=0.0.0.0:5001", "--threads=4", "service:app"]
