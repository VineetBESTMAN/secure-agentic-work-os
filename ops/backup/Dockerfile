FROM postgres:16-alpine

RUN apk add --no-cache aws-cli bash coreutils tzdata

COPY backup.sh restore-verify.sh entrypoint.sh /opt/workos/
RUN chmod 0555 /opt/workos/*.sh

USER postgres
ENTRYPOINT ["/opt/workos/entrypoint.sh"]
