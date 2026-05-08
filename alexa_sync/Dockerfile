ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache py3-pip python3 \
    && pip3 install --break-system-packages websockets==13.0.1

COPY run.sh /
COPY app.py /app.py

RUN chmod a+x /run.sh

CMD ["/run.sh"]
