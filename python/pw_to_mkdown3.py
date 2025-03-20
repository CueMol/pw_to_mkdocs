from functools import partial
import os
import re
import shutil
import argparse
import urllib.parse
from pathlib import Path
import logging
import chardet


# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)1.1s %(module)s:%(funcName)s] %(message)s",
)

logger = logging.getLogger(__name__)


def strip_quotes(text):
    if len(text) >= 2:
        if (text[0] == text[-1]) and text[0] in ['"', "'", "`"]:
            return text[1:-1]
    return text


def try_decode(encoded_bytes):
    try:
        return encoded_bytes.decode("euc_jp")
    except UnicodeDecodeError:
        pass

    try:
        return encoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_name(file_base):
    hex_str = str(file_base)
    decoded_bytes = bytes.fromhex(hex_str)
    decoded_part = try_decode(decoded_bytes)
    # decoded_part = re.sub(r'[\\/:*?"<>|]+', "_", decoded_part)
    decoded_part = re.sub(r'[\\:*?"<>|]+', "_", decoded_part)
    return decoded_part


def process_image_options(parts):
    options = []
    zoom_link = True
    for i in parts[1:]:
        if i == "nolink":
            zoom_link = False
            continue
        mm = re.match(r"(.+)%", i)
        if mm is not None:
            # options.append(f'width="{mm.group(1)}%"')
            scl = float(mm.group(1)) / 100.0
            options.append(f'style="zoom: {scl}"')

    if zoom_link:
        options.append(".on-glb")

    return options


class PukiWikiToMkDocsConverter:
    def __init__(self, output_dir, img_dir="assets/images"):
        self.output_dir = Path(output_dir)
        self.img_dir = self.output_dir / img_dir

        # 出力ディレクトリの作成
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir.mkdir(parents=True, exist_ok=True)

        # PukiWiki特有の記法を変換するためのルール
        self.pukiwiki_rules = [
            (r"^#access$", r""),
            (r"^#contents$", r""),
            # quote
            (r"^>([^>].*)$", r"\n>\1"),
            (r"^>>([^>].*)$", r"\n>>\1"),
            # line break
            (r"^(.+)~$", r"\1<br />"),
            # comment
            (r"^//(.+)$", r""),
            # 整形済みテキスト
            (r"^ (.+)$", r"```\n\1\n```"),
            # 見出し変換 (*-->#, **→##,***!→###)
            (r"^\*\s*([^\s\*].+)$", r"## \1"),
            (r"^\*\*\s*([^\s\*].+)$", r"### \1"),
            (r"^\*\*\*\s*([^\s\*].+)$", r"#### \1"),
            # リスト
            (r"^\-\-\-([^\-].+)$", r"\n        - \1"),
            (r"^\-\-([^\-].+)$", r"\n    - \1"),
            (r"^\-([^\-].+)$", r"\n- \1"),
            # 番号付きリスト
            (r"^\+\+\+(.+)$", r"    1. \1"),
            (r"^\+\+(.+)$", r"  1. \1"),
            (r"^\+(.+)$", r"1. \1"),
            # # 表組み (簡易対応)
            # (r"\|(.+)\|", r"|\1|"),
            # 太字
            (r"''(.+?)''", r"**\1**"),
            # 斜体
            (r"'''(.+?)'''", r"*\1*"),
            # 取り消し線
            (r"%%(.+?)%%", r"~~\1~~"),
            # 下線
            (r"__(.+?)__", r"<u>\1</u>"),
        ]

        # 画像のパターン (PukiWikiでは&ref=xxxxx.jpg などが一般的)
        self.img_pattern = re.compile(r"&ref\(([^)]+)\);")
        # self.img_pattern2 = re.compile(r"^#ref\(([^)]+)\)")
        self.img_pattern2 = r"^#ref\(([^)]+)\)"

        # 内部リンクパターン
        # self.internal_link_pattern = re.compile(r"\[\[([^>\]]+)(?:>(.+))?\]\]")
        self.int_link_pat1 = re.compile(r"\[\[([^>\]]+)>([^>\]]+)\]\]")
        self.int_link_pat2 = re.compile(r"\[\[([^:\]]+):([^>\]]+)\]\]")
        self.int_link_pat3 = re.compile(r"\[\[([^>\]]+)\]\]")

        # def_list
        self.def_list_pat = r"^:(.+)\|(.+)$"

    def is_default_lang(self, lang=None):
        if lang is None:
            lang = self.lang
        return lang == "ja"

    def _detect_encoding(self, file_path):
        """ファイルのエンコーディングを検出する関数"""
        with open(file_path, "rb") as f:
            raw_data = f.read(4096)  # 最初の4096バイトで判定
            result = chardet.detect(raw_data)
            encoding = result["encoding"]
            confidence = result["confidence"]

            # 日本語の一般的なエンコーディングを優先
            if encoding and encoding.lower() in [
                "utf-8",
                "shift_jis",
                "euc-jp",
                "iso-2022-jp",
                "cp932",
            ]:
                return encoding

            # 検出結果が不明確な場合は、一般的な日本語エンコーディングを試す
            encodings_to_try = ["utf-8", "shift_jis", "euc-jp", "cp932", "iso-2022-jp"]

            for enc in encodings_to_try:
                try:
                    with open(file_path, "r", encoding=enc) as test_f:
                        test_f.read()
                    # logger.info(f"エンコーディングを {enc} に決定しました: {file_path}")
                    return enc
                except UnicodeDecodeError:
                    continue

            # すべて失敗した場合は、検出結果を使用
            logger.warning(
                f"確実なエンコーディングを特定できませんでした。検出結果を使用: {encoding}"
            )
            return encoding or "utf-8"

    def _convert_internal_links(self, content, page_name):
        """内部リンクを変換する関数"""

        def _repl(match, page_name):
            if len(match.groups()) == 2:
                text = match.group(1)
                link = match.group(2) if match.group(2) else text
            else:
                link = match.group(1)
                text = link

            mm = re.search(r"^\./(.+)$", link)
            if mm is not None:
                # relative link (1)
                rel = mm.group(1)
                # logger.info(f"*** {link=} {rel=}")
                # logger.info(f"[{text}](/{self.lang}/{page_name}/{rel})")

                if self.is_default_lang():
                    return f"[{text}](/{page_name}/{rel})"
                else:
                    return f"[{text}](/{self.lang}/{page_name}/{rel})"

            mm = re.search(r"^\.\./(.*)$", link)
            if mm is not None:
                # relative link (2)
                rel = mm.group(1)
                parent_dir = page_name.parent
                logger.info(f"*** {parent_dir=} {rel=}")

                if self.is_default_lang():
                    result = f"[{text}](/{parent_dir}/{rel})"
                else:
                    result = f"[{text}](/{self.lang}/{parent_dir}/{rel})"

                # logger.info(f"*** {result=}")
                return result

            # http://www.cuemol.org/en/index.php?cuemol2%2FBallStickRenderer
            mm = re.search(r"/(\w+)/index\.php\?(.+)", link)
            if mm is not None:
                # abs link (URL)
                lang = mm.group(1)
                page_name = mm.group(2)
                page_name = urllib.parse.unquote(page_name)
                page_name = re.sub(r'[\\:*?"<>|]+', "_", page_name)
                if self.is_default_lang(lang):
                    return f"[{text}](/{page_name})"
                else:
                    return f"[{text}](/{lang}/{page_name})"

            mm = re.search(r"^http://", link)
            if mm is not None:
                # external link
                return f"[{text}]({link})"

            if self.is_default_lang():
                return f"[{text}](/{link})"
            else:
                return f"[{text}](/{self.lang}/{link})"

        result = self.int_link_pat1.sub(partial(_repl, page_name=page_name), content)
        result = self.int_link_pat2.sub(partial(_repl, page_name=page_name), result)
        result = self.int_link_pat3.sub(partial(_repl, page_name=page_name), result)
        return result

    def _process_images(self, content, page_name):
        """画像の参照を処理する関数"""

        def _repl(match, para=False):
            img_info = match.group(1)

            # カンマで分割されている場合はパラメータを解析
            parts = img_info.split(",")
            img_path = strip_quotes(parts[0].strip())
            # alt_text = "" if len(parts) < 2 else parts[1].strip()
            alt_text = Path(img_path).stem

            options = process_image_options(parts)

            # 画像ファイル名の取得
            img_filename = os.path.basename(img_path)

            # save_path = self.img_dir / page_name / img_filename

            # Markdown形式の画像参照に変換
            rel_path = os.path.join(
                "/", "assets", "images", page_name, img_filename
            ).replace("\\", "/")
            logger.info(f"{rel_path=}")
            if len(options) > 0:
                options = " ".join(options)
                result = f"![{alt_text}]({rel_path}){{ {options} }}"
            else:
                result = f"![{alt_text}]({rel_path})"
            if para:
                return f"\n{result}\n"
            else:
                return result

        result = self.img_pattern.sub(partial(_repl, para=False), content)
        result = re.sub(
            self.img_pattern2, partial(_repl, para=True), result, flags=re.MULTILINE
        )
        return result

    def _conv_def_list(self, content):
        def _replace(m):
            dt = m.group(1)
            dd = m.group(2)
            # print(f"{dt=}: {dd=}")
            return f"{dt}\n:   {dd}\n"

        result = re.sub(self.def_list_pat, _replace, content, flags=re.MULTILINE)
        return result

    def determine_target_filename(self, src_file):
        """変換後のファイル名を決定する関数"""
        file_name = os.path.basename(src_file)
        file_base = os.path.splitext(file_name)[0]
        logger.info(f"{file_name=}")
        logger.info(f"{file_base=}")

        decoded_part = decode_name(file_base)
        logger.info(f"{decoded_part=}")
        # target_dir = self.output_dir / self.lang
        if decoded_part == "FrontPage":
            decoded_part = "index"

        return Path(f"{decoded_part}.md")

    def convert_pukiwiki_file(self, source_file):
        """PukiWikiファイルをMarkdownファイルに変換する関数"""
        # ファイルのエンコーディングを検出
        encoding = self._detect_encoding(source_file)

        # ソースファイルを読み込む
        with open(source_file, "r", encoding=encoding, errors="replace") as f:
            orig_content = f.read()

        # 出力ファイル名の決定
        target_file = self.determine_target_filename(source_file)
        if str(target_file) == "FormatRule.md":
            logger.info(f"skip: {target_file=}")
            return

        page_name = target_file.with_suffix("")
        logger.info(f"{page_name=}")

        # 内部リンクの変換
        content = self._convert_internal_links(orig_content, page_name)

        content = self._conv_def_list(content)

        # 画像の処理
        # logger.info(f"{target_file.with_suffix('')=}")
        content = self._process_images(content, page_name)

        # PukiWiki記法をMarkdownに変換
        for pattern, replacement in self.pukiwiki_rules:
            try:
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            except Exception as e:
                logger.error(f"re.sub error: {pattern=} {replacement=}")
                raise e

        # Markdownファイルとして保存
        out_file = self.output_dir / self.lang / target_file
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        orig_path = out_file.parent / f"{out_file.stem}.pwtxt"
        logger.info(f"{source_file} -> {orig_path}")
        with orig_path.open("w", encoding="utf-8") as f:
            f.write(orig_content)
        # shutil.copyfile(source_file, orig_path)

        logger.info(f"変換完了: {source_file} -> {out_file}")
        return True

    def process_attach(self, src_file):
        file_name = os.path.basename(src_file)
        file_base = os.path.splitext(file_name)[0]
        spl = file_base.split("_")
        assert len(spl) == 2
        page_name = decode_name(spl[0])
        ref_name = decode_name(spl[1])
        if page_name == "FrontPage":
            page_name = "index"

        target_dir = self.img_dir / page_name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, target_dir / f"{ref_name}")

        logger.info(f"{str(target_dir)} / {str(ref_name)}")

    def batch_convert_directory(self, source_dir):
        """ディレクトリ内のすべてのPukiWikiファイルを変換する関数"""

        source_path = Path(source_dir)

        attach_files = list(source_path.glob("ja/attach/**/*"))
        # attach_files = []
        for path in attach_files:
            if path.suffix == "":
                logger.info(f"processing: {path}")
                self.process_attach(path)

        if self.lang == "ja":
            source_files = list(source_path.glob("ja/wiki/**/*.txt"))
        elif self.lang == "en":
            source_files = list(source_path.glob("ja/wiki.en/**/*.txt"))

        # source_files = list(source_path.glob(f"{self.lang}wiki/**/46726F6E7450616765.txt"))
        # source_files = list(
        #     source_path.glob("wiki/**/6375656D6F6C322F5475626552656E6465726572.txt")
        # )

        # with ThreadPoolExecutor() as executor:
        #     results = list(executor.map(self.convert_pukiwiki_file, source_files))
        for path in source_files:
            # for path in source_files[:5]:
            logger.info(f"processing: {path}")
            try:
                self.convert_pukiwiki_file(path)
            except Exception as e:
                logger.error(f"Error in {path}Error: {str(e)}", exc_info=e)


def main():
    parser = argparse.ArgumentParser(description="PukiWikiからMkDocsへの移行ツール")
    parser.add_argument("--source-dir", help="ローカルPukiWikiファイルのディレクトリ")
    parser.add_argument("--output-dir", required=True, help="出力ディレクトリ")
    parser.add_argument(
        "--site-name", default="Migrated Documentation", help="MkDocsサイト名"
    )
    parser.add_argument(
        "--start-page",
        default="FrontPage",
        help="開始ページ名（インデックスページが利用できない場合）",
    )

    args = parser.parse_args()

    converter = PukiWikiToMkDocsConverter(
        args.output_dir,
    )

    converter.lang = "ja"
    converter.batch_convert_directory(args.source_dir)
    converter.lang = "en"
    converter.batch_convert_directory(args.source_dir)


if __name__ == "__main__":
    main()
