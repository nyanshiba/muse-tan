# X Thread

X の投稿を、ブラウザもログインも API キーもなしに読むための道具です。ポスト単体から、作者のスレッド全体、返信、引用、長文（ノート）と X Article の全文までを、公開情報だけから組み立てます。

実体は Python 標準ライブラリのみで書かれた単一ファイル（`bin/x_thread.py`）なので、Muse がなくてもコマンドラインの道具として使えます。Muse で使う場合は、このフォルダをカスタムコネクタとして配置し、相方のスキル [`x_post_fetch`](../../custom-skills/x_post_fetch/SKILL.md) と一緒に使います。使い方の要点はその SKILL.md に、エージェント向けの詳細仕様は [CONNECTOR.md](CONNECTOR.md) にあります。

## 設計思想

元は nyanshiba の OpenCode プラグイン `x-thread` です。設計の要点は原典のこの2か所にあります。

- x.com と syndication の呼び出しを直列化し、合計2本の keep-alive 接続で回す。NAT セッションを抑えるための設計です — [index.ts L102](https://github.com/nyanshiba/harness-bonsai/blob/6e32b7673495966de274a2fb5fb7bd7defc92e8e/.config/opencode/plugins/x-thread/index.ts#L102) [README L76](https://github.com/nyanshiba/harness-bonsai/blob/6e32b7673495966de274a2fb5fb7bd7defc92e8e/.config/opencode/plugins/x-thread/README.md#L76)
- 入力 URL は（作者， ポスト ID）に分解し、正規形の URL で取りに行く。fxtwitter や oEmbed 対応は中華モデルの RLHF の癖を反映したものなので、Muse には不要でしょうが、クエリパラメータを除去する機能は非機能要件として維持すべきです — [statusTarget, index.ts L30-L49](https://github.com/nyanshiba/harness-bonsai/blob/6e32b7673495966de274a2fb5fb7bd7defc92e8e/.config/opencode/plugins/x-thread/index.ts#L30-L49)

移植にあたって足した設計は、トランスポートまわりと、画像と記事の取得です。

- 接続はホストごとに keep-alive 1本を再利用します。Python の urllib はリクエストごとに `Connection: close` を送り接続を再利用できないため、小さなトランスポートを自前で実装しています
- Muse の VM では、出口が forward proxy 経由に固定され、プロキシは環境変数で指定されます。プロキシのある環境では CONNECT トンネルを張ります。forward proxy エンドポイントは実行時に環境変数から読むだけで、コードには埋め込んでいません。プロキシのない環境ではそのまま直結します
- `--direct-network`：forward proxy を介さず origin へ直結するモード。名前解決は hosts ファイルの pin と DoH で行い、失敗してもプロキシへフォールバックしない
- `--media`：画像のみ、small バリアントをファイルに保存してパスを返す。動画・GIF は非対応
- 記事（X Article）は、引用で出てきたときにリンクしか返ってこないことに気付いて、本文まで返るように直した。

## 注意

- 使っている syndication エンドポイントは X の非公式・未文書化のものです。予告なく変更・廃止される可能性があります
- 個別の投稿を読むための道具です。一括収集やポーリングには使わないでください

原典 `x-thread`： nyanshiba（MIT）。移植と拡張： Museたん。