ARG DEPENDENCY_IMAGE
FROM ${DEPENDENCY_IMAGE}
ARG REVISION
ARG OPERATIONS_SHA256
LABEL org.opencontainers.image.revision=$REVISION
LABEL org.wtdw.operations.sha256=$OPERATIONS_SHA256
# The locally preserved dependency image was built by Compose. Its project
# labels must not make standalone probes appear to belong to the live project.
LABEL com.docker.compose.project=""
LABEL com.docker.compose.service=""
LABEL com.docker.compose.version=""
# Dependencies come from an explicitly inspected, saved local image. Never pip
# install or copy a mutable checkout into a release artifact.
RUN rm -rf /app && mkdir /app
COPY backend/ /app/
COPY runtime/ /opt/wtdw/
COPY backend/entrypoint.sh /entrypoint.sh
COPY backend/guarded-migrate-entrypoint.sh /guarded-migrate-entrypoint.sh
RUN chmod 755 /entrypoint.sh /guarded-migrate-entrypoint.sh
WORKDIR /app
ENTRYPOINT ["/entrypoint.sh"]
