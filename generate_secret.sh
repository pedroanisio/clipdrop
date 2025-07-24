# file_path: generate_secret.sh
#!/bin/bash

# Check if .env file exists, create if it doesn't
if [ ! -f .env ]; then
    touch .env
fi

# Generate a new secret key using Python
SECRET_KEY=$(python3 -c "import os; print(os.urandom(24).hex())")

# Add or update the SECRET_KEY in the .env file
if grep -q "SECRET_KEY" .env; then
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
else
    echo "SECRET_KEY=$SECRET_KEY" >> .env
fi

echo ".env file has been updated with a new SECRET_KEY."
