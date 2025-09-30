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

# Clone and build Cyclone DDS
RUN git clone https://github.com/eclipse-cyclonedds/cyclonedds.git
WORKDIR /workspace/cyclonedds
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local -DENABLE_TYPELIB=ON -DBUILD_IDLC=ON -DBUILD_TOOLS=ON && \
    make -j$(nproc) && \
    make install

# Update library cache
RUN ldconfig

# Set environment variables
ENV CYCLONEDDS_HOME=/usr/local
ENV LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
ENV PATH=/usr/local/bin:$PATH

# Install Python dependencies
WORKDIR /app
RUN pip3 install --upgrade pip

# Try to install cyclonedds Python bindings
RUN pip3 install cyclonedds || echo "cyclonedds pip install failed, using alternative approach"

# Copy application files
COPY test_app.py .
COPY alternative_test.py .
COPY simple_test.py .

# Copy SpatialDDS v1.3 files
COPY spatialdds.idl .
COPY spatialdds_test.py .
COPY spatialdds_validation.py .
COPY http_binding.py .
COPY comprehensive_test.py .

# Create a non-root user
RUN useradd -m -u 1000 ddsuser && chown -R ddsuser:ddsuser /app
USER ddsuser

# Expose DDS ports
EXPOSE 7400-7500/udp

# Default command - run comprehensive tests
CMD ["python3", "comprehensive_test.py"]