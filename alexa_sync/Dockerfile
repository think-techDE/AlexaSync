ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3

COPY run.sh /
COPY app.py /app.py

RUN chmod a+x /run.sh

CMD ["/run.sh"]
