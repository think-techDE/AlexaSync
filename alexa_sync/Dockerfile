ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/community" > /etc/apk/repositories \
    && echo "http://dl-cdn.alpinelinux.org/alpine/edge/main" >> /etc/apk/repositories \
    && apk add --no-cache chromium chromium-chromedriver py3-pip python3 \
    && pip3 install --break-system-packages selenium==4.23.1 websockets==13.0.1

COPY run.sh /
COPY app.py /app.py

RUN chmod a+x /run.sh

CMD ["/run.sh"]
