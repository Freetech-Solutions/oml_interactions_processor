# OMniLeads: Analisis de sentimiento

#### This project is part of OMniLeads - 100% Open-Source Contact Center Software

#### [Community discord](https://discord.gg/FEDkVmSQ)


This component is responsible for processing call recordings from the telephony channel. One of its functionalities is to convert recordings in WAV format to MP3 and upload them to an S3 bucket. Additionally, when recordings from separate channels (client and agent) are activated, the component removes silence before uploading the MP3 files to the bucket. This facilitates the manipulation of these files for further analysis or transcription.

##Environment Variables

The following list of environment variables needs to be passed when running the component as part of the OMniLeads suite.

```
GEARMAN_HOST
S3_BUCKET_NAME
S3_ENDPOINT
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_DEFAULT_REGION
```

Requests are received through a Gearman Job Server (tel_callrec) queue and are sent from an Asterisk ACD instance that has just finished generating a recording of a telephone call.




