ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache chromium chromium-chromedriver py3-aiohttp py3-pip python3

COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /requirements.txt

COPY run.sh /
COPY *.py /
COPY index.html /

RUN chmod a+x /run.sh

CMD ["/run.sh"]
