#!/usr/bin/python3
###################################################
# pwdecode.py :
#      Ver.0 2024-02-23
# ■ 概要
#     PukiWiki の wiki/ 以下のファイル名はエンコードされている。
#     これを読めるようにする。実用上は、ls を使うはずなので、
#     ls に対してパイプラインで実行することを想定している。
# ■ 使用例
#     ls -ltr | cat -n | pwdecode.py
#     ※ cat -n を入れておくと、実際のファイル名と簡単に対応付けられる。
###################################################
import sys
import re

# PukiWikiは、utf-8 と euc_jp とがある。成功した方を採用。
def try_decode(encoded_bytes):
    try:
        return encoded_bytes.decode('euc_jp')
    except UnicodeDecodeError:
        pass  

    try:
        return encoded_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return None

# 各行のデコード
def decode_pukiwiki_filename(encoded_strings):
    decoded_filename = ''
    # hex_pattern = re.compile(r'([0-9A-F]{4,})(?=\.txt$)') # エンコードされたファイル名部分を抽出
    hex_pattern = re.compile(r'([0-9A-F]{4,})') # エンコードされたファイル名部分を抽出
    pos = 0

    for match in hex_pattern.finditer(encoded_strings):
        start, end = match.span()
        dirname =encoded_strings[pos:start]
        decoded_filename += encoded_strings[pos:start]  # 16進数でない部分を追加
        
        hex_str = match.group(1)
        decoded_bytes = bytes.fromhex(hex_str)
        decoded_part = try_decode(decoded_bytes)
        if decoded_part is not None:
            decoded_filename += decoded_part
        else:
            decoded_filename += hex_str  # デコードに失敗した場合は元の16進数の文字列を追加
        pos = end
        print(f"{decoded_part}: {dirname}/{hex_str}.txt")

    decoded_filename += encoded_strings[pos:]  # 残りの部分を追加

    return decoded_filename

# 標準入力を受け取り、エンコードされた部分だけをデコード。
for line in sys.stdin:
    line = line.strip()
    decoded_filename = decode_pukiwiki_filename(line)
    # print(decoded_filename)
