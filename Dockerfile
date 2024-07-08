# Use the official Python 3.11 Alpine image as the base image
FROM python:3.11-alpine

# Install dependencies and AWS CLI v2
RUN apk add --no-cache \
    curl \
    unzip \
    bash \
    docker-cli && \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip -q awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws

# Add AWS CLI to PATH
ENV PATH="/usr/local/aws-cli/v2/current/bin:$PATH"

# Set the working directory
WORKDIR /action

# Set the PYTHONPATH to include the /action directory
ENV PYTHONPATH /action

# Copy the action and src files
COPY action.yaml ./action.yaml
COPY src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir -r ./src/requirements.txt

# Set the entrypoint to run the Python script as a module using bash
#ENTRYPOINT ["bash", "-c", "python -m src.cli"]
ENTRYPOINT ["python", "-m", "src.cli"]
