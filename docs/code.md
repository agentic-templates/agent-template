# Code guide

Write code that a reader understands the first time they read it. If a rule in "Write functions a reader can follow" or "Comment only what the code can't show" would make a piece of code harder to read, choose the clearer code and explain why in the pull request.

## Keep the design simple

- Build the simplest design that meets what the project needs now, including any accuracy or speed that the issue asks for. Add a layer, an option or a general version of something only when the project needs it, such as a storage interface once it has a second storage backend, not in case it gets one.
- Remove dead code in the code that your change touches. For dead code elsewhere, open an issue.
- Follow the conventions the code already uses for naming style, file layout and error handling, even where this guide would choose differently. But if the code around yours hides errors by convention, don't hide them in the code you add, though your code may still call that code. If a convention makes code harder to read, open an issue. To change any convention, use an issue and change it everywhere in a pull request of its own.
- Write shell scripts for bash 3.2, which macOS ships.
- Keep each decision the code makes, such as a limit or how a total is rounded, in one place, so that changing it takes one edit. Before you write code that makes a decision, check whether other code already makes it, and use that code if it does. Keep separate decisions apart even when they look alike. For example, if the limits on a description and on a category name are both 50 characters, use a constant for each, because either limit can change without the other.
- Keep the code that calculates a result apart from the code that reads input or writes output, such as files, the network, the screen, the clock, random numbers and environment variables. The calculating code reads nothing but its arguments, and does nothing but return its result. For example, the function that totals this month's expenses takes the expenses and today's date as arguments and returns the total. The code that calls it reads the expense file and the clock, and prints the result.
- Fix the cause of a problem, not only the place where it shows up. For example, if an expense may have no category, and listing expenses crashes on such an expense, change the code that reads expenses so that it gives that expense the category "none". Don't catch the crash in the code that prints the list.
- If the code you need to change is hard to change as it is, refactor it first. Refactor only the code your change touches, and leave it at least as easy to read as before.
- A refactoring must pass every test without changing what any test checks. If no test runs the code, write one before you refactor. If a test fails after the refactoring and you can't make it pass, undo the part of the refactoring that fails it.
- If fixing the cause, together with any refactoring that the fix needs, would make the pull request too big to review in one sitting, open an issue for the larger work. Mark the issue you're working on as blocked by it, as step 4 of "Make a change" in AGENTS.md describes.

## Write functions a reader can follow

- Give each function one purpose, and name it for that purpose.
- Return early instead of nesting conditions.
- Prefer a plain loop and named steps to a clever one-liner.
- Choose names that explain themselves, in whole words. Name a boolean as a yes-or-no question, such as `is_empty`.

## Add complexity only for a requirement or a measurement

Keep the complex code in one place behind a plain interface, so that the code that calls it doesn't need to know how it works.

- Complexity is required when a simpler version would fail a requirement. Examples are code that must reach the accuracy or speed an issue asks for, and code that a bug fix adds and that a reader might later think is unnecessary, such as a special case. Name the requirement in a comment. A bug fix that adds no such code adds no complexity, and needs no comment that names the requirement.
- Complexity is a choice when a simpler version already meets the requirements and the complex one does better, such as running faster, using less memory or giving more accurate results. Make that choice only where a measurement shows what the complex version gains. Say in a comment what the code gains and where the measurement is, such as a benchmark name or an issue.
- Keep tests that check the complex code's results. For speed or memory, keep a plain version in the test code, such as the code before you optimized it, and check that both give the same results. For accuracy, keep the test cases that measure it, so that a later change can't make the results worse without anyone noticing.

## Comment only what the code can't show

- A comment gives a reason, a hidden constraint or a surprise. It doesn't repeat what the code says.
- Make the code clearer before you add a comment. A better name or a smaller function often makes the comment unnecessary.

For example, in an expense tracker, this comment gives a reason that the code can't show: "Amounts are whole cents, because a fraction such as 0.10 can't be stored exactly, and the errors add up in a total."

## Fail with a clear error

- Check input where it enters the program: command-line arguments, environment variables, files, network traffic and, in a library, the arguments to its public functions. Trust values that come from inside the program.
- Catch an error only where you can recover from it or add useful context. Never hide an error, such as by returning an empty list when the expense file can't be read.

## Test behavior

- Test what the code does through its public interface, not how it does it.
- When you write a test before you refactor code that no test runs, take its expected values from the results that the code gives before the refactoring. A result is anything the code gives: what it returns, prints or writes, its exit status and its errors. Check every result, including details that no requirement asks for, such as the order of a list, because a refactoring must keep every result the same.
- Such a test expects the code's result even where your issue or the project's docs give another value, because a test that expects that value would fail before the refactoring and after it. If the docs give a value that the code doesn't give, open a `bug` issue for the difference, unless one is open already.
- In every other test, check the result against what the requirement says: a fixed expected value, or a rule that must hold for every input, such as "reading a saved expense back gives the same expense". Don't take the expected value from the code's own output, and don't compute it the way the code does, because then the test repeats the code instead of checking it. The tests may still compare optimized code with a plain version of it.
- When you write a test, other than one before you refactor code that no test runs, don't check what the requirement leaves open, such as the order of a list when the requirement doesn't ask for a particular order.
- Test the edges of each requirement as well as a typical case: empty input, and the values on both sides of each limit, such as files of 50,000 and 50,001 rows.
- After any refactoring that your change needs, and before you change what the code does, write or change the tests for the behavior that your issue adds, changes or fixes. Take the expected results from the issue, and run the tests to see them fail. Then change the code until they pass. If a test for any other requirement fails on what it expects, rather than on how it calls the code, fix the code, not the test.
- Name each test for the behavior and the condition, such as "leaves an expense in a foreign currency out of the total".
- Keep tests independent of the order in which they run, and of anything else that can differ from one run to the next, such as the network, the clock and random numbers. Give the code fixed values or stand-ins only for those. Never use a stand-in for the project's own code, except for code that does nothing but read the clock or send requests over the network, such as a function that returns today's date.
- Never weaken a test. You weaken a test when you change it so that it passes for code that made it fail before, while the requirement it checks stays the same. Skipping a test weakens it too. Changing how a test calls code whose interface you changed doesn't weaken it, and neither does changing what it expects to the result that your issue asks for.
- Delete a test only when your issue removes its requirement, or when your issue is about the time the checks take and the test costs more than it's worth, such as one that checks only what other tests check already.
- If you think a test is wrong and your issue doesn't ask for another result, leave it as it is and open an issue for it.
- Say in the pull request which tests you changed or deleted, and why.
- Write code that works for every input, not only for the inputs in the tests. For example, don't return a fixed total when the amounts match the ones in a test.
- When the checks on pull requests take longer than the time limit under "Commands" in AGENTS.md, make them faster. Run tests in parallel, run a test only when a change touches what it tests, and delete the tests that cost more than they're worth. Move slow tests of the product as a whole, such as end-to-end tests, into a command that CI runs after each merge to main, and list it under "Commands".
- Don't loosen a check to make it pass, such as by turning off a rule or relaxing a limit, unless your issue asks for it. Add a comment or attribute that makes a check skip some code, such as `# noqa`, only when you can't fix that code without making it worse, and give the reason in it.
- If the project's coverage check fails on a line that no test runs, delete the line if the project doesn't need it. Otherwise, write a test that runs it. Mark the line as excluded from coverage only when no test can run it, such as code that runs only on another operating system, and say why in the marker.
- List in the pull request each coverage exclusion marker that it adds.
