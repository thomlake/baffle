## Tools

Use `uv` to run commands and manage dependencies.

```shell
uv run python <script>
uv add <package-name>
```

## Coding Conventions

- Prefer modern Python typing conventions like `list[str]`
- Don't import `annotations` from `__future__`
- Add a blank line after scope blocks
- Write useful comments that explain the "why"
- Always prefer immediate failure/error (do not perform excessive error handling that can hide bugs)
