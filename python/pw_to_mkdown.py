#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob
import shutil
import argparse
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
import requests
from concurrent.futures import ThreadPoolExecutor
import logging
import chardet

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("migration.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class PukiWikiToMkDocsConverter:
    def __init__(self, pukiwiki_base_url, output_dir, img_dir="assets/images", download_images=True, php_mode=False):
        """
        PukiWikiからMkDocsへの変換を行うクラス

        Args:
            pukiwiki_base_url (str): PukiWikiサイトのベースURL
            output_dir (str): 出力ディレクトリ
            img_dir (str): 画像を保存するディレクトリ（output_dir内の相対パス）
            download_images (bool): 画像をダウンロードするかどうか
            php_mode (bool): index.php?PageName 形式のURLを使用するかどうか
        """
        self.pukiwiki_base_url = pukiwiki_base_url
        self.output_dir = Path(output_dir)
        self.img_dir = self.output_dir / img_dir
        self.download_images = download_images
        self.php_mode = php_mode
        
        # URLからベースディレクトリとindex.phpの位置を特定
        if pukiwiki_base_url and php_mode:
            # URLからindex.phpの位置を特定
            if 'index.php' in pukiwiki_base_url:
                # index.phpを含むURLが渡された場合
                self.base_dir = pukiwiki_base_url.split('index.php')[0]
                self.php_file = 'index.php'
            else:
                # ベースURLのみが渡された場合
                self.base_dir = pukiwiki_base_url
                self.php_file = 'index.php'
            
            logger.info(f"PHP Mode: BaseDir={self.base_dir}, PHPFile={self.php_file}")
        
        # 出力ディレクトリの作成
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir.mkdir(parents=True, exist_ok=True)
        
        # PukiWiki特有の記法を変換するためのルール
        self.pukiwiki_rules = [
            # 見出し変換 (!!!→#, !!→##, !→###)
            (r'^!!!(.+)$', r'# \1'),
            (r'^!!(.+)$', r'## \1'),
            (r'^!(.+)$', r'### \1'),
            
            # リスト
            (r'^\-\-\-(.+)$', r'    * \1'),
            (r'^\-\-(.+)$', r'  * \1'),
            (r'^\-(.+)$', r'* \1'),
            
            # 番号付きリスト
            (r'^\+\+\+(.+)$', r'    1. \1'),
            (r'^\+\+(.+)$', r'  1. \1'),
            (r'^\+(.+)$', r'1. \1'),
            
            # 整形済みテキスト
            (r'^ (.+)$', r'```\n\1\n```'),
            
            # 表組み (簡易対応)
            (r'\|(.+)\|', r'|\1|'),
            
            # 太字
            (r"''(.+?)''", r'**\1**'),
            
            # 斜体
            (r"'''(.+?)'''", r'*\1*'),
            
            # 取り消し線
            (r'%%(.+?)%%', r'~~\1~~'),
            
            # 下線
            (r'__(.+?)__', r'<u>\1</u>'),
        ]
        
        # 画像のパターン (PukiWikiでは&ref=xxxxx.jpg などが一般的)
        self.img_pattern = re.compile(r'&ref\(([^)]+)\)')
        
        # 内部リンクパターン
        self.internal_link_pattern = re.compile(r'\[\[([^>]+)(?:>(.+))?\]\]')
        
    def _download_image(self, image_url, save_path):
        """画像をダウンロードする関数"""
        try:
            response = requests.get(image_url, stream=True)
            if response.status_code == 200:
                with open(save_path, 'wb') as file:
                    response.raw.decode_content = True
                    shutil.copyfileobj(response.raw, file)
                logger.info(f"画像をダウンロードしました: {save_path}")
                return True
            else:
                logger.error(f"画像のダウンロードに失敗しました: {image_url} Status: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"画像のダウンロード中にエラーが発生しました: {image_url} Error: {str(e)}")
            return False
            
    def _convert_internal_links(self, content):
        """内部リンクを変換する関数"""
        def _replace_internal_link(match):
            link = match.group(1)
            text = match.group(2) if match.group(2) else link
            
            # URLエンコード部分をデコード
            link = urllib.parse.unquote(link)
            
            # 特殊文字を処理
            link = link.replace(' ', '-').lower()
            
            # .mdファイルへのリンクにする
            return f'[{text}]({link}.md)'
            
        return self.internal_link_pattern.sub(_replace_internal_link, content)
    
    def _process_images(self, content):
        """画像の参照を処理する関数"""
        def _replace_image_ref(match):
            img_info = match.group(1)
            
            # カンマで分割されている場合はパラメータを解析
            parts = img_info.split(',')
            img_path = parts[0].strip()
            alt_text = "" if len(parts) < 2 else parts[1].strip()
            
            # 画像ファイル名の取得
            img_filename = os.path.basename(img_path)
            
            # 画像のURLと保存先パス (PHPモードかどうかで分岐)
            if self.php_mode:
                # index.php?plugin=attach&openfile=xxx 形式の対応
                img_url = f"{self.base_dir}{self.php_file}?plugin=attach&openfile={urllib.parse.quote(img_path)}"
            else:
                # 標準的な /attached/xxx 形式
                img_url = f"{self.pukiwiki_base_url}/attached/{urllib.parse.quote(img_path)}"
                
            save_path = self.img_dir / img_filename
            
            # 画像のダウンロード
            if self.download_images:
                self._download_image(img_url, save_path)
            
            # Markdown形式の画像参照に変換
            rel_path = os.path.join("assets", "images", img_filename).replace("\\", "/")
            return f'![{alt_text}]({rel_path})'
            
        return self.img_pattern.sub(_replace_image_ref, content)
        
    def _detect_encoding(self, file_path):
        """ファイルのエンコーディングを検出する関数"""
        with open(file_path, 'rb') as f:
            raw_data = f.read(4096)  # 最初の4096バイトで判定
            result = chardet.detect(raw_data)
            encoding = result['encoding']
            confidence = result['confidence']
            
            logger.info(f"エンコーディング検出: {file_path} => {encoding} (信頼度: {confidence:.2f})")
            
            # 日本語の一般的なエンコーディングを優先
            if encoding and encoding.lower() in ['utf-8', 'shift_jis', 'euc-jp', 'iso-2022-jp', 'cp932']:
                return encoding
            
            # 検出結果が不明確な場合は、一般的な日本語エンコーディングを試す
            encodings_to_try = ['utf-8', 'shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
            
            for enc in encodings_to_try:
                try:
                    with open(file_path, 'r', encoding=enc) as test_f:
                        test_f.read()
                    logger.info(f"エンコーディングを {enc} に決定しました: {file_path}")
                    return enc
                except UnicodeDecodeError:
                    continue
            
            # すべて失敗した場合は、検出結果を使用
            logger.warning(f"確実なエンコーディングを特定できませんでした。検出結果を使用: {encoding}")
            return encoding or 'utf-8'
    
    def convert_pukiwiki_file(self, source_file, target_file=None):
        """PukiWikiファイルをMarkdownファイルに変換する関数"""
        try:
            # ファイルのエンコーディングを検出
            encoding = self._detect_encoding(source_file)
            
            # ソースファイルを読み込む
            with open(source_file, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()
                
            # 内部リンクの変換
            content = self._convert_internal_links(content)
            
            # 画像の処理
            content = self._process_images(content)
            
            # PukiWiki記法をMarkdownに変換
            for pattern, replacement in self.pukiwiki_rules:
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            
            # 出力ファイル名の決定
            if target_file is None:
                file_name = os.path.basename(source_file)
                file_base = os.path.splitext(file_name)[0]
                target_file = self.output_dir / f"{file_base}.md"
            
            # Markdownファイルとして保存
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(content)
                
            logger.info(f"変換完了: {source_file} -> {target_file}")
            return True
            
        except Exception as e:
            logger.error(f"ファイル変換中にエラーが発生しました: {source_file} Error: {str(e)}")
            return False
    
    def scrape_pukiwiki_pages(self, index_url):
        """PukiWikiのインデックスページからすべてのページを取得する関数"""
        try:
            response = requests.get(index_url)
            
            # Content-Typeからエンコーディングを取得
            content_type = response.headers.get('Content-Type', '')
            if 'charset=' in content_type:
                encoding = content_type.split('charset=')[1].split(';')[0].strip()
            else:
                encoding_result = chardet.detect(response.content)
                encoding = encoding_result['encoding']
            
            # レスポンスをデコード
            try:
                html_content = response.content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                # フォールバック
                try:
                    html_content = response.content.decode('utf-8', errors='replace')
                except:
                    html_content = response.text
            
            soup = BeautifulSoup(html_content, 'html.parser')
            pages = []
            
            # PHPモード用のリンク抽出ロジック
            if self.php_mode:
                # index.php?PageName 形式のリンクを検索
                all_links = soup.find_all('a')
                for link in all_links:
                    href = link.get('href')
                    if href and self.php_file in href and '?' in href:
                        parts = href.split('?')
                        if len(parts) > 1:
                            # クエリパラメータを抽出
                            query = parts[1]
                            if '&' in query:
                                # クエリに&が含まれる場合は最初のパラメータのみ取得
                                page_name = query.split('&')[0]
                            else:
                                page_name = query
                            
                            # アンカー部分(#xxx)を除去
                            if '#' in page_name:
                                page_name = page_name.split('#')[0]
                                
                            # 特定のプラグインURLは除外
                            if not any(plugin in page_name for plugin in ['plugin=', 'cmd=']):
                                pages.append(urllib.parse.unquote(page_name))
            else:
                # 標準的なPukiWikiリンク
                links = soup.select('div.list a')
                for link in links:
                    href = link.get('href')
                    if href and "?" in href:  # PukiWikiのページリンクはクエリ文字列を含む
                        page_name = href.split('?')[1]
                        pages.append(urllib.parse.unquote(page_name))
            
            # 重複を除去
            pages = list(set(pages))
            logger.info(f"スクレイピングしたページ数: {len(pages)}, ページ名の例: {pages[:5] if pages else 'なし'}")
            
            return pages
        except Exception as e:
            logger.error(f"インデックスページのスクレイピング中にエラーが発生しました: {index_url} Error: {str(e)}")
            return []
    
    def scrape_and_convert_page(self, page_name):
        """指定されたページをスクレイピングして変換する関数"""
        try:
            # ページのURLを構築（PHPモードかどうかで分岐）
            if self.php_mode:
                # index.php?PageName&source 形式
                page_url = f"{self.base_dir}{self.php_file}?{urllib.parse.quote(page_name)}&source"
            else:
                # 標準的な ?PageName&source 形式
                page_url = f"{self.pukiwiki_base_url}?{urllib.parse.quote(page_name)}&source"
                
            logger.info(f"スクレイピングURL: {page_url}")
            
            # ソースの取得
            response = requests.get(page_url)
            if response.status_code != 200:
                logger.error(f"ページの取得に失敗しました: {page_name} Status: {response.status_code}")
                return False
            
            # レスポンスのエンコーディング検出
            content_type = response.headers.get('Content-Type', '')
            if 'charset=' in content_type:
                encoding = content_type.split('charset=')[1].split(';')[0].strip()
                logger.info(f"Content-Typeからエンコーディングを検出: {encoding}")
            else:
                # Content-Typeにエンコーディング情報がない場合は自動検出
                encoding_result = chardet.detect(response.content)
                encoding = encoding_result['encoding']
                logger.info(f"自動検出したエンコーディング: {encoding} (信頼度: {encoding_result['confidence']:.2f})")
            
            # レスポンスのデコード
            try:
                decoded_content = response.content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                logger.warning(f"指定されたエンコーディング {encoding} でデコードできませんでした。UTF-8を試みます。")
                try:
                    decoded_content = response.content.decode('utf-8')
                except UnicodeDecodeError:
                    logger.warning("UTF-8でもデコードできませんでした。Shift_JISを試みます。")
                    try:
                        decoded_content = response.content.decode('shift_jis')
                    except UnicodeDecodeError:
                        logger.warning("Shift_JISでもデコードできませんでした。EUC-JPを試みます。")
                        try:
                            decoded_content = response.content.decode('euc-jp')
                        except UnicodeDecodeError:
                            logger.warning("EUC-JPでもデコードできませんでした。CP932を試みます。")
                            try:
                                decoded_content = response.content.decode('cp932')
                            except UnicodeDecodeError:
                                logger.error("すべてのエンコーディング試行が失敗しました。errors='replace'で強制的にデコードします。")
                                decoded_content = response.content.decode('utf-8', errors='replace')
            
            # ソースをテンポラリファイルに保存
            temp_file = self.output_dir / f"temp_{page_name}.txt"
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(decoded_content)
            
            # 変換
            safe_page_name = page_name.replace('/', '_')
            target_file = self.output_dir / f"{safe_page_name}.md"
            result = self.convert_pukiwiki_file(temp_file, target_file)
            
            # テンポラリファイルの削除
            os.remove(temp_file)
            
            return result
        except Exception as e:
            logger.error(f"ページのスクレイピング・変換中にエラーが発生しました: {page_name} Error: {str(e)}")
            return False
            
    def batch_convert_directory(self, source_dir):
        """ディレクトリ内のすべてのPukiWikiファイルを変換する関数"""
        source_path = Path(source_dir)
        source_files = list(source_path.glob('**/*.txt'))
        
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(self.convert_pukiwiki_file, source_files))
            
        success_count = results.count(True)
        fail_count = results.count(False)
        logger.info(f"バッチ変換完了: 成功 {success_count}, 失敗 {fail_count}")
        
    def batch_convert_website(self, index_url):
        """PukiWikiサイト全体を変換する関数"""
        pages = self.scrape_pukiwiki_pages(index_url)
        
        logger.info(f"スクレイピングしたページ数: {len(pages)}")
        
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(self.scrape_and_convert_page, pages))
            
        success_count = results.count(True)
        fail_count = results.count(False)
        logger.info(f"サイト全体の変換完了: 成功 {success_count}, 失敗 {fail_count}")
        
    def generate_mkdocs_yml(self, site_name="My Documentation"):
        """MkDocs設定ファイルを生成する関数"""
        # Markdownファイルのリストを取得
        md_files = list(self.output_dir.glob('*.md'))
        md_files = [str(file.relative_to(self.output_dir)) for file in md_files]
        
        # 基本設定の作成
        config = f"""site_name: {site_name}
site_description: Migrated from PukiWiki
site_author: Auto-generated

theme:
  name: material
  language: ja
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - search.highlight
    - search.suggest

markdown_extensions:
  - pymdownx.highlight
  - pymdownx.superfences
  - admonition
  - footnotes
  - toc:
      permalink: true

nav:
  - ホーム: index.md
"""
        
        # ナビゲーションの構築
        for file in sorted(md_files):
            if file != "index.md":  # index.mdは既に追加済み
                name = os.path.splitext(file)[0].replace('_', ' ')
                config += f"  - {name}: {file}\n"
        
        # ファイルとして保存
        config_path = self.output_dir.parent / "mkdocs.yml"
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config)
            
        logger.info(f"MkDocs設定ファイルを生成しました: {config_path}")

def main():
    parser = argparse.ArgumentParser(description='PukiWikiからMkDocsへの移行ツール')
    parser.add_argument('--url', help='PukiWikiサイトのベースURL')
    parser.add_argument('--source-dir', help='ローカルPukiWikiファイルのディレクトリ')
    parser.add_argument('--output-dir', required=True, help='出力ディレクトリ')
    parser.add_argument('--site-name', default='Migrated Documentation', help='MkDocsサイト名')
    parser.add_argument('--no-images', action='store_true', help='画像をダウンロードしない')
    parser.add_argument('--encoding', help='強制的に指定するエンコーディング（自動検出をスキップ）')
    parser.add_argument('--php-mode', action='store_true', help='index.php?PageName 形式のURLを使用する')
    parser.add_argument('--start-page', default='FrontPage', help='開始ページ名（インデックスページが利用できない場合）')
    
    args = parser.parse_args()
    
    if not args.url and not args.source_dir:
        parser.error("--url または --source-dir のいずれかを指定してください")
        
    # エンコーディングが指定されている場合、検出関数をオーバーライド
    if args.encoding:
        logger.info(f"エンコーディングを {args.encoding} に強制設定します")
        
        def force_encoding(self, file_path):
            return args.encoding
    
    converter = PukiWikiToMkDocsConverter(
        args.url if args.url else "",
        args.output_dir,
        download_images=not args.no_images,
        php_mode=args.php_mode
    )
    
    # エンコーディングが指定されている場合、検出関数をオーバーライド
    if args.encoding:
        converter._detect_encoding = lambda self, file_path: args.encoding
    
    if args.source_dir:
        converter.batch_convert_directory(args.source_dir)
    else:
        if args.php_mode and args.start_page:
            # PHP形式で特定のスタートページから開始
            logger.info(f"PHP Mode: スタートページ '{args.start_page}' から変換を開始します")
            # まずスタートページを変換
            converter.scrape_and_convert_page(args.start_page)
            
            # リンクを辿って他のページも変換
            try:
                # インデックスがあれば使用
                pages = converter.scrape_pukiwiki_pages(args.url)
                if pages:
                    for page in pages:
                        converter.scrape_and_convert_page(page)
                else:
                    logger.warning("インデックスページからのリンク取得に失敗しました。スタートページのみ変換します。")
            except Exception as e:
                logger.error(f"インデックスページのスクレイピング中にエラーが発生しました: {e}")
        else:
            # 通常のモード
            converter.batch_convert_website(args.url)
    
    converter.generate_mkdocs_yml(args.site_name)
    
    logger.info("移行プロセスが完了しました")
    logger.info(f"MkDocs環境を立ち上げるには、以下のコマンドを実行してください:")
    logger.info(f"cd {os.path.dirname(args.output_dir)} && mkdocs serve")

if __name__ == "__main__":
    main()
    
