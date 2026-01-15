import re
import requests
import sys

def get_key_from_yaml():
    """
    Fetch API key from the Bundestag OpenAPI YAML definition.
    
    Returns:
        None: Prints the key to stdout on success
        
    Exits:
        0: Success
        1: Error occurred
    """
    # Use the technical definition file instead of the website
    url = "https://search.dip.bundestag.de/api/v1/openapi.yaml"
    
    try:
        # Enable SSL verification and set timeout to prevent hanging
        response = requests.get(url, timeout=10, verify=True)
        response.raise_for_status()
        
        content = response.text
        
        # Search for the pattern in the YAML file:
        # description: "Example: *ApiKey OSOegLs...*"
        # The regex looks for "ApiKey" followed by a space and captures the key
        match = re.search(r"ApiKey\s+([A-Za-z0-9\._\-]+)", content)
        
        if match:
            clean_key = match.group(1).strip()
            print(clean_key)
            sys.exit(0)
        else:
            print("ERROR: No key found in YAML file.", file=sys.stderr)
            sys.exit(1)

    except requests.exceptions.RequestException as e:
        print(f"CRITICAL ERROR: Failed to fetch API key: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"CRITICAL ERROR: Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    get_key_from_yaml()