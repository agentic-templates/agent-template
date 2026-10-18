# Pages guide

## Organize a page

- Headings name what the reader does or learns, in words they would search for. Write "Install on macOS", not "Getting started".
- Use numbered steps for a procedure, bullets for items of the same kind, and prose for reasoning.
- Put each command the reader runs in a code block they can copy. Show the output when it helps them check their work.
- Make link text name the page it opens.
- Update a page in place when something changes. Don't add notes such as "Updated in May".

## Write a README

A README opens with what the reader can do with the project. Then it covers these parts, in this order, each under a heading that follows the rule above:

1. A demo of the project as it works today, if there is one: a screenshot, a recording or a live link.
2. Setup.
3. One example from start to finish.

Until the example ends, explain only what the reader needs in order to use the project. After the example, state the project's known limits. A section on how the project works, if the README needs one, goes after the limits. Move it to `docs/` when it grows longer than the rest of the README.

## Review a changed page with a fresh reader

Before you open the pull request, have a reader who has seen only the page review each README or docs page where you added or rewrote text:

1. Run `scripts/fresh-reader "<reader>" <page>`, where `<reader>` is the page's reader and what they want to do, such as "a developer who wants to use this project". The script prints the sentences that the reader had to read twice or didn't need, each with the reader's reason. If `claude` isn't installed, start a new agent with no other context, and give it the page's text and the prompt in `scripts/fresh-reader`, with `<reader>` filled in.
2. In the text you added or rewrote, rewrite each sentence that you agree is unclear, so that it says the same thing in clearer words, and cut each one that you agree isn't needed. Leave the other sentences on the page as they are.
