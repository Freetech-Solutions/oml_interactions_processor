FROM python:3.10-alpine as run

RUN apk add --no-cache bash
WORKDIR /app
COPY fastagi.py requirements.txt /app/
RUN pip install -r ./requirements.txt

CMD ["python", "fastagi.py"]