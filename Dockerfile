# SpatialDDS demo image built on top of the prebuilt Cyclone DDS base
FROM ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04

WORKDIR /app

# Copy the SpatialDDS 1.8 demo files. AR-demo files live under ar_demo/ on
# the host but are flattened into /app inside the container so existing
# in-container entry points (`python3 spatialdds_test.py`, etc.) keep working.
#
# **These baked copies are what `docker run cyclonedds-python ...` runs
# without a bind mount** -- and until the 1.8 migration this set copied no
# generated tree at all, while `spatialdds_demo` (which it does copy) imports
# one. The test lanes bind-mount the working tree instead (scripts/
# run_tests.sh), because an image built in August went on passing a lane for
# two months against code that no longer existed. Rebuild after changing any
# file below if you mean to run the baked copies.
COPY spatialdds_idl18 ./spatialdds_idl18
COPY spatialdds_demo_idl18 ./spatialdds_demo_idl18
COPY spatialdds_test.py .
COPY spatialdds_validation.py .
COPY spatialdds_demo ./spatialdds_demo
COPY ar_demo/spatialdds.idl .
COPY ar_demo/spatialdds_demo_client.py .
COPY ar_demo/spatialdds_demo_server.py .
COPY ar_demo/spatialdds_bootstrap_server.py .
COPY ar_demo/spatialdds_catalog_server.py .
COPY ar_demo/spatialdds_demo_tests.py .
COPY ar_demo/http_binding.py .
COPY ar_demo/comprehensive_test.py .
COPY ar_demo/run_all_tests.sh .
COPY ar_demo/catalog_seed.json .
COPY cyclonedds.xml /etc/cyclonedds.xml
COPY idl ./idl
COPY manifests ./manifests

# Create a non-root user
RUN useradd -m -u 1000 ddsuser && chown -R ddsuser:ddsuser /app
USER ddsuser

# Expose DDS ports
EXPOSE 7400-7500/udp

# Default command - run comprehensive tests
CMD ["python3", "comprehensive_test.py"]
