# The robot tier: nav2 on arm64, with no simulator underneath it.
#
# Gazebo has no arm64 build (Part 4's pre-flight: neither gazebo-ros-pkgs nor
# ros-gz-sim is packaged for Humble on arm64), and emulating amd64 to run a
# physics engine for a flat plaza would be a great deal of machinery to
# reproduce very little. What this demo needs from a robot is a pose that
# moves plausibly and a planner that respects the model's declared bounds.
#
# So: real nav2, and a kinematic sim beneath it. nav2 plans; PlazaSim supplies
# odom and tf and consumes cmd_vel. Nothing here fakes a sensor, which is the
# whole argument of the keep-out design -- policy, not perception.
FROM ros:humble-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-nav2-bringup \
      ros-humble-nav2-costmap-2d \
      ros-humble-nav2-navfn-planner \
      ros-humble-nav2-controller \
      ros-humble-nav2-lifecycle-manager \
      ros-humble-nav2-map-server \
      ros-humble-nav2-msgs \
      ros-humble-tf2-ros \
      ros-humble-rmw-cyclonedds-cpp \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# This repo's own CycloneDDS, beside ROS's.
#
# ROS Humble ships CycloneDDS 0.10.5 for its middleware; this repo's Python
# binding is 11.0.1 and its generated types were produced by that idlc. Both
# live here, and that is fine: the binding loads its own libddsc from
# /usr/local and never shares symbols with rmw_cyclonedds_cpp's. The existing
# ROS tier has run both in one process since long before Part 4 -- which is
# the fact that makes wire-level interop possible at all, and which an earlier
# version of this file missed by simply not installing the binding.
#
# Built from source because there is no aarch64 wheel for 11.0.1: pip falls
# back to the sdist, which needs a Cyclone 11 C library to compile against.
RUN apt-get update && apt-get install -y --no-install-recommends       build-essential cmake git libssl-dev pkg-config     && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN git clone --depth 1 --branch 11.0.1 https://github.com/eclipse-cyclonedds/cyclonedds.git
WORKDIR /opt/cyclonedds
RUN mkdir build && cd build &&     cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local -DBUILD_SHARED_LIBS=ON              -DENABLE_SECURITY=OFF -DBUILD_DDSPERF=OFF -DBUILD_IDLC=ON              -DBUILD_TOOLS=ON &&     make -j$(nproc) && make install
RUN ldconfig
ENV CYCLONEDDS_HOME=/usr/local
ENV PATH=/usr/local/bin:$PATH
RUN python3 -m pip install --no-cache-dir cyclonedds==11.0.1
RUN python3 -c "import importlib.metadata as m; print('cyclonedds', m.version('cyclonedds'))"     && idlc -h >/dev/null

# The same CycloneDDS config the demo image carries. `require_dds_env`
# insists on it by path, and both stacks in this container read it happily --
# it says only "pick an interface". Pointing CYCLONEDDS_URI at a file that is
# not there is worse than not setting it: ROS's middleware fails to
# initialise with "rmw handle is invalid", which reads like a ROS problem.
RUN printf '%s\n' \
    '<?xml version="1.0" encoding="UTF-8"?>' \
    '<CycloneDDS>' \
    '  <Domain id="any">' \
    '    <General>' \
    '      <Interfaces>' \
    '        <NetworkInterface autodetermine="true"/>' \
    '      </Interfaces>' \
    '    </General>' \
    '  </Domain>' \
    '</CycloneDDS>' > /etc/cyclonedds.xml

WORKDIR /ws
COPY requirements.txt /ws/
RUN pip3 install --no-cache-dir -r /ws/requirements.txt

COPY . /ws/
ENV PYTHONPATH=/ws
