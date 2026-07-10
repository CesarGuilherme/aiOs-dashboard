# Use a slim python image for a small footprint
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the current directory into the container
COPY . .

# Expose the default dashboard port
EXPOSE 8181

# Start the dashboard in headless mode (no browser attempt)
# We use 0.0.0.0 for HOST so it's reachable outside the container
CMD ["python3", "cli.py", "dashboard", "--no-open"]
