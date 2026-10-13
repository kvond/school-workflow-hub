#!/usr/bin/env python3
"""Build one new local School Morning.app without opening apps or changing Chrome."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

SOURCE=Path(__file__).resolve().parent
CHROME_APP=Path('/Applications/Google Chrome.app')

def checked_profile(local_state, profile, school_account):
    if not re.fullmatch(r'Profile [0-9]+', profile):
        raise ValueError('Choose one explicitly verified named Chrome profile.')
    if not isinstance(school_account,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',school_account):
        raise ValueError('Supply the school account for the profile metadata check.')
    local_state=Path(local_state).expanduser().resolve()
    data=json.loads(local_state.read_text())
    item=data.get('profile',{}).get('info_cache',{}).get(profile,{})
    if item.get('user_name')!=school_account or not (local_state.parent/profile).is_dir():
        raise ValueError('The selected profile does not match the supplied school account.')
    return local_state.parent

def sandbox_policy(chrome_data):
    def literal(path):
        value=str(path)
        if any(c in value for c in '\r\n\0'):
            raise ValueError('Chrome data path cannot contain a control character.')
        return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'
    # Seatbelt matches canonical paths; /tmp and /var aliases on macOS must
    # not accidentally turn a deny into an ineffective literal.
    chrome_data=Path(chrome_data).expanduser().resolve()
    guards=' '.join('(literal '+literal(chrome_data/name)+')'
                    for name in ('SingletonLock','SingletonSocket','SingletonCookie'))
    return '(version 1)\n(allow default)\n(deny signal)\n(allow signal (target same-sandbox))\n(deny file-write* '+guards+')\n'

def validate_output(output):
    output=Path(output).expanduser().absolute()
    if output.suffix!='.app' or output.exists() or output.is_symlink():
        raise ValueError('Choose a new .app path; existing artifacts are never replaced.')
    if '\n' in str(output) or '\r' in str(output):
        raise ValueError('The output path cannot contain a newline.')
    return output

def build(output,profile,extension_id,local_state,school_account):
    output=validate_output(output)
    if not re.fullmatch('[a-p]{32}',extension_id):
        raise ValueError('Supply the existing installed extension ID.')
    chrome_data=checked_profile(local_state,profile,school_account)
    policy=sandbox_policy(chrome_data)
    with (CHROME_APP/'Contents/Info.plist').open('rb') as stream:
        if plistlib.load(stream).get('CFBundleIdentifier')!='com.google.Chrome':
            raise ValueError('The official Chrome application was not found.')
    if not (CHROME_APP/'Contents/MacOS/Google Chrome').is_file():
        raise ValueError('The official Chrome executable was not found.')
    subprocess.run(['/usr/bin/xcrun','--find','clang'],check=True,capture_output=True)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.mkdir(mode=0o700)  # exclusive creation: never overwrite a previous app
    contents=output/'Contents'; resources=contents/'Resources'; macos=contents/'MacOS'
    resources.mkdir(parents=True); macos.mkdir()
    with (contents/'Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleIdentifier':'local.school-morning.launcher',
                      'CFBundleName':'School Morning','CFBundleDisplayName':'School Morning',
                      'CFBundlePackageType':'APPL','CFBundleExecutable':'school-morning',
                      'CFBundleVersion':'1','CFBundleShortVersionString':'1.0',
                      'LSUIElement':True,'NSHighResolutionCapable':True},stream)
    for name in ('launch.sh',): shutil.copyfile(SOURCE/name,resources/name)
    (resources/'launch.sh').chmod(0o700)
    (resources/'launch.conf').write_text('\n'.join(('school-morning/v1',profile,extension_id,str(CHROME_APP),str(chrome_data)))+'\n')
    (resources/'launch.conf').chmod(0o600)
    (resources/'handoff.sb').write_text(policy)
    (resources/'handoff.sb').chmod(0o600)
    subprocess.run(['/usr/bin/xcrun','clang','-O2','-Wall','-Wextra','-framework','Cocoa',
                    str(SOURCE/'app-main.m'),'-o',str(macos/'school-morning')],check=True,capture_output=True)
    subprocess.run(['/usr/bin/xcrun','clang','-O2','-Wall','-Wextra',str(SOURCE/'bounded-handoff.c'),
                    '-o',str(resources/'bounded-handoff')],check=True,capture_output=True)
    return output

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--extension-id',required=True)
    parser.add_argument('--local-state',type=Path,required=True)
    parser.add_argument('--school-account',required=True)
    args=parser.parse_args()
    try:
        result=build(args.output,args.profile,args.extension_id,args.local_state,args.school_account)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        parser.error('The local app could not be built safely. '+type(error).__name__)
    print('Created: '+str(result))
    print('Not launched. Reload the packaged helper with morning-setup.html before using it.')

if __name__=='__main__': main()
