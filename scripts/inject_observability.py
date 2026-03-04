import os
import sys
import yaml
import argparse
import copy
import socket
import tomllib

def get_free_port(default_port=None):
    """
    Checks if the default_port is available. 
    If it is taken (or not provided), finds and returns an available port on the host.
    """
    if default_port:
        # Test if the default port is free by attempting to bind to it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('localhost', default_port))
                return default_port # It's free!
            except socket.error:
                pass # It's taken, move on to finding a random one

    # If default is taken, bind to port 0 to let the OS pick a random open port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

# Standard SRE Services Definition
CADVISOR_CONFIG = {
    'image': 'gcr.io/cadvisor/cadvisor:v0.47.1', 
    'platform': 'linux/amd64',
    'container_name': 'sds-cadvisor',
    'ports': ['8082:8080'],                      
    'volumes': [
        '/:/rootfs:ro',
        '/var/run:/var/run:ro',
        '/sys:/sys:ro',
        '/var/lib/docker/:/var/lib/docker:ro',
        '/dev/disk/:/dev/disk:ro'
    ],
    'privileged': True,
    'restart': 'always',
    'devices': ['/dev/kmsg']
}

# UPDATED: Now includes prometheus_data volume and command arguments
PROMETHEUS_CONFIG = {
    'image': 'prom/prometheus:latest',
    'platform': 'linux/amd64',
    'container_name': 'sds-prometheus',
    'ports': ['9090:9090'],
    'volumes': [
        './prometheus.yml:/etc/prometheus/prometheus.yml',
        'prometheus_data:/prometheus'
    ],
    'command': [
        '--config.file=/etc/prometheus/prometheus.yml',
        '--storage.tsdb.path=/prometheus'
    ],
    'restart': 'always',
    'depends_on': ['cadvisor']
}

def is_dynamic_injection_enabled(sds_root):
    """Reads sds.toml to check if the feature flag is active."""
    toml_path = os.path.join(sds_root, 'sds.toml')
    
    if not os.path.exists(toml_path):
        return False
        
    try:
        with open(toml_path, "rb") as f:
            config = tomllib.load(f)
            # UPDATED: Look inside the 'operator' block instead of 'features'
            return config.get('operator', {}).get('dynamic_observability_injection', False)
    except Exception as e:
        print(f"⚠️ Could not read sds.toml: {e}")
        return False

def generate_prometheus_config(service_names):
    """
    Generates a prometheus.yml that scrapes cAdvisor AND tries to scrape 
    all app services.
    """
    config = {
        'global': {'scrape_interval': '15s'},
        'scrape_configs': [
            {
                'job_name': 'prometheus',
                'static_configs': [{'targets': ['localhost:9090']}]
            },
            {
                'job_name': 'cadvisor',
                # NOTE: This stays 8080 because Prometheus uses the internal Docker network
                'static_configs': [{'targets': ['cadvisor:8080']}]
            }
        ]
    }
    
    app_targets = [f"{name}:8080" for name in service_names if "db" not in name and "redis" not in name]
    if app_targets:
        config['scrape_configs'].append({
            'job_name': 'app-services',
            'metrics_path': '/metrics',
            'static_configs': [{'targets': app_targets}]
        })

    return config

def inject_observability(app_dir, sds_root): # FIX 1: Passed sds_root as a parameter
    compose_path = os.path.join(app_dir, 'docker-compose.yml')
    
    if not os.path.exists(compose_path):
        print(f"❌ No docker-compose.yml found in {app_dir}")
        return

    print(f"🔍 Analyzing {app_dir}...")

    with open(compose_path, 'r') as f:
        try:
            compose_data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(f"❌ Error parsing YAML: {exc}")
            return

    services = compose_data.get('services', {})

    if 'cadvisor' in services or 'prometheus' in services:
        print(f"⚠️  Observability already exists in {app_dir}. Skipping.")
        return

    # Check the feature flag
    use_dynamic_features = is_dynamic_injection_enabled(sds_root)

    # FIX 2 & 3: Consolidated logic so we only map and assign ports once based on the flag
    if use_dynamic_features:
        print("💉 Feature Flag ON: Injecting DYNAMIC cAdvisor and Prometheus...")
        cadvisor_host_port = get_free_port(8082)
        prometheus_host_port = get_free_port(9090)
        
        app_cadvisor_config = copy.deepcopy(CADVISOR_CONFIG)
        app_cadvisor_config['ports'] = [f"{cadvisor_host_port}:8080"]

        app_prometheus_config = copy.deepcopy(PROMETHEUS_CONFIG)
        app_prometheus_config['ports'] = [f"{prometheus_host_port}:9090"]
        
    else:
        print("💉 Feature Flag OFF: Injecting LEGACY cAdvisor and Prometheus...")
        app_cadvisor_config = copy.deepcopy(CADVISOR_CONFIG)
        app_cadvisor_config['ports'] = ["8080:8080"] 
        app_cadvisor_config.pop('platform', None) # Remove ARM fix

        app_prometheus_config = copy.deepcopy(PROMETHEUS_CONFIG)
        app_prometheus_config['ports'] = ["9090:9090"]
        app_prometheus_config.pop('platform', None) # Remove ARM fix for Prometheus too!

    # Actually assign our modified configs to the services dictionary
    services['cadvisor'] = app_cadvisor_config
    services['prometheus'] = app_prometheus_config

    compose_data['services'] = services

    # Inject Top-Level Volume for Prometheus Data
    if 'volumes' not in compose_data or compose_data['volumes'] is None:
        compose_data['volumes'] = {}
    
    if 'prometheus_data' not in compose_data['volumes']:
        compose_data['volumes']['prometheus_data'] = None

    # Write back Docker Compose
    with open(compose_path, 'w') as f:
        yaml.dump(compose_data, f, default_flow_style=False, sort_keys=False)

    # Generate prometheus.yml
    prom_config = generate_prometheus_config(services.keys())
    prom_path = os.path.join(app_dir, 'prometheus.yml')
    
    with open(prom_path, 'w') as f:
        yaml.dump(prom_config, f, default_flow_style=False, sort_keys=False)

    print(f"✅ Successfully injected observability into {app_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Inject Prometheus/cAdvisor into an app.')
    parser.add_argument('--target', type=str, help='Specific app directory', required=False)
    parser.add_argument('--all', action='store_true', help='Scan all apps in sds/apps/')
    
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sds_root = os.path.dirname(script_dir)
    apps_root = os.path.join(sds_root, 'apps')

    if args.target:
        inject_observability(args.target, sds_root) # FIX 1: Pass sds_root
    elif args.all:
        print(f"🚀 Starting Fleet Injection in {apps_root}")
        for root, dirs, files in os.walk(apps_root):
            if 'docker-compose.yml' in files:
                if '.sds' not in root:
                    inject_observability(root, sds_root) # FIX 1: Pass sds_root
    else:
        print("Please specify --target <path> or --all")

