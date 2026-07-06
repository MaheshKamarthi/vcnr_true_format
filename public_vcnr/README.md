Place `.vcnr` files in this folder to publish them with GitHub Pages.

If you want the browser player to show a built-in sample button automatically,
name one of the files:

```text
public_vcnr/sample.vcnr
```

The generated site writes that information into `sample-config.json`, so the
browser player can detect the sample without making a failing request for a
missing media file.

After `build_github_pages.py` runs, these files are copied to:

```text
site/media/
```

If your repository becomes a GitHub Pages project site, a file such as:

```text
public_vcnr/movie.vcnr
```

is published at:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/media/movie.vcnr
```

You can then open the hosted player and paste that URL, or share it directly
through the player's `?url=` parameter.

The hosted player also accepts a custom sample URL through:

```text
?sample=https%3A%2F%2Fexample.com%2Fmovie.vcnr
```
