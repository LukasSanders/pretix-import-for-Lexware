import argparse
import subprocess
import sys
from pathlib import Path

import yaml


PLACEHOLDERS = {
    'api_settings.lexware.api_key': '<YOUR_SECRET_API_KEY>',
    'api_settings.pretix.api_token': '<YOUR_SECRET_API_TOKEN>',
}


def read_staged_settings() -> str:
    try:
        result = subprocess.run(
            ['git', 'show', ':settings.yaml'],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
        )
    except subprocess.CalledProcessError:
        raise ValueError('settings.yaml is not present in the Git index.') from None
    return result.stdout


def check_settings(content: str) -> list[str]:
    try:
        settings = yaml.safe_load(content)
    except yaml.YAMLError:
        return ['settings.yaml is not valid YAML.']

    if not isinstance(settings, dict):
        return ['settings.yaml must contain a YAML mapping.']

    errors = []
    for path, placeholder in PLACEHOLDERS.items():
        value = settings
        for key in path.split('.'):
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]

        if not isinstance(value, str) or value.strip() != placeholder:
            errors.append(f'{path} must be the placeholder {placeholder}.')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Ensure staged API credentials in settings.yaml are placeholders.'
    )
    parser.add_argument(
        '--file',
        type=Path,
        help='Check a settings file directly instead of the staged settings.yaml.',
    )
    args = parser.parse_args()

    try:
        content = args.file.read_text(encoding='utf-8') if args.file else read_staged_settings()
    except OSError:
        print('Unable to read the settings file.', file=sys.stderr)
        return 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    errors = check_settings(content)
    if errors:
        for error in errors:
            print(f'Commit blocked: {error}', file=sys.stderr)
        print('Replace real credentials with placeholders before committing.', file=sys.stderr)
        return 1

    print('Settings credential check passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
