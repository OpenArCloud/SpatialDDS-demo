# SpatialDDS demo image built on top of the prebuilt Cyclone DDS base
FROM ghcr.io/openarcloud/cyclonedds-python-base:0.10.5-ubuntu22.04

WORKDIR /app

# Copy SpatialDDS v1.4 files
COPY spatialdds.idl .
COPY spatialdds_demo ./spatialdds_demo
COPY spatialdds_demo_client.py .
COPY spatialdds_demo_server.py .
COPY spatialdds_vps_server.py .
COPY spatialdds_catalog_server.py .
COPY spatialdds_demo_tests.py .
COPY spatialdds_test.py .
COPY spatialdds_validation.py .
COPY http_binding.py .
COPY comprehensive_test.py .
COPY run_all_tests.sh .
COPY catalog_seed.json .
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
