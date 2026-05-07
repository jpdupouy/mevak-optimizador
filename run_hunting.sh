#!/bin/bash
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY ~/.zshrc | cut -d'"' -f2)
export TAVILY_API_KEY=$(grep TAVILY_API_KEY ~/.zshrc | cut -d'"' -f2)
export ACUAIA_EMAIL_PASSWORD=$(grep ACUAIA_EMAIL_PASSWORD ~/.zshrc | cut -d'"' -f2)
cd /Users/jeanpierredupouycerda/mi-agente
/usr/bin/python3 hunting_mevak_v3.py
