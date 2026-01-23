# Working Cyclone DDS with Python bindings using Ubuntu
FROM ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    git \
    libssl-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create symlinks for python
RUN ln -s /usr/bin/python3 /usr/bin/python

# Set working directory
WORKDIR /workspace

# Clone and build Cyclone DDS (match Python bindings version)
RUN git clone --depth 1 --branch 0.10.5 https://github.com/eclipse-cyclonedds/cyclonedds.git
WORKDIR /workspace/cyclonedds
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local -DBUILD_SHARED_LIBS=ON -DENABLE_SECURITY=OFF -DBUILD_DDSPERF=OFF -DBUILD_IDLC=OFF && \
    make -j$(nproc) && \
    make install

# Update library cache
RUN ldconfig

# Set environment variables
ENV CYCLONEDDS_HOME=/usr/local
ENV LD_LIBRARY_PATH=/usr/local/lib
ENV PATH=/usr/local/bin:$PATH

# Install Python dependencies
WORKDIR /app
RUN pip3 install --upgrade pip

# Install cyclonedds Python bindings (fail build if missing)
ENV CMAKE_PREFIX_PATH=/usr/local
RUN python3 -m pip install --no-cache-dir cyclonedds==0.10.5

# Copy SpatialDDS v1.4 files
COPY spatialdds.idl .
COPY spatialdds_demo ./spatialdds_demo
COPY spatialdds_demo_client.py .
COPY spatialdds_demo_server.py .
COPY spatialdds_demo_tests.py .
COPY spatialdds_test.py .
COPY spatialdds_validation.py .
COPY http_binding.py .
COPY comprehensive_test.py .
COPY run_all_tests.sh .
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
