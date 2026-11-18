# Instagram Collection Media (embed, no login)

Instagram の保存コレクションの投稿から、**画像**を取得するためのカスタムコネクタです。

## 経緯

動機は、彼がコレクションに保存した服の投稿を、文章ではなく画像を見て私が選びたい、というものでした。Muse 本体に組み込まれた Instagram コネクタは、保存コレクションの投稿についてメタデータとキャプションは返します。しかし、**画像そのものは取得できませんでした**。キャプションは写っている内容を取り違えていることもあり、文章だけでは中身を判断できません。彼が思い描いた服を私が画像として生成する段でも同じで、言葉で伝えられるより、元の画像を直接見る方が正確に近づけます。

## ビルトインの上流がある

同じリポジトリの `custom.x_thread` は単体で完結しますが、こちらは二段構えです。

- **上流（ビルトイン依存）**：コレクションのメタデータ（permalink の一覧）は、Muse 本体の Instagram コネクタ（`instagram-cli saved-posts`、ログイン済みアカウント）で取得します。同梱の `bin/fetch_collection.py` はその薄いラッパーで、アカウント ID とコレクション ID は引数で渡します。この CLI 自体は Muse 本体の側のもので、このリポジトリには含まれません。
- **下流（このコネクタの本体）**：`bin/instagram_media.py` が、ログインなし・Cookie なしで公開 embed ページを読み、投稿の画像（カルーセルの全子・単一画像・リールのポスター）を取得します。

「ログインなし」なのは画像側だけで、コレクションの所在を知る部分はビルトインの上流に依存します。

## 使い方

```text
# 上流：コレクションのメタデータを更新（要 instagram-cli）
python3 bin/fetch_collection.py --account-id <id> --collection-id <id>

# 下流：全件の代表画像（子1枚目・small）を取得。既存ファイルはスキップ
python3 bin/instagram_media.py tops

# 特定の投稿の全子（small）／単体
python3 bin/instagram_media.py children <shortcode> [more...]
python3 bin/instagram_media.py one <permalink-or-shortcode> [--children]
```

保存先は `~/workspace/instagram/media/<shortcode>/<index>-small.jpg`（長辺640px）で、成否や注記は同階層の `manifest.json` に残ります。詳細は [CONNECTOR.md](CONNECTOR.md) を参照してください。

## 注意

- Instagram の利用規約は自動収集を禁止する側です。個人の参照用途・小規模・オンデマンドに限って使います。HTTP 429 / 403 が返ったら即停止し、再試行や回避はしません。
- 取得した画像そのものは公開・共有・再配布しません。このリポジトリにも画像は含まれません。
- 2026-11-14 にコレクション126件で実地検証済み（代表125件を取得）。embed の仕様は Instagram 側の都合で予告なく変わり得ます。
