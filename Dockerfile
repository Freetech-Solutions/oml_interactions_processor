FROM python:3.10-alpine as run

RUN apk add --no-cache bash
WORKDIR /fastagi
COPY . .
RUN pip install -r ./requirements.txt

CMD ["python", "/fastagi/server/server_pystrix.py"]