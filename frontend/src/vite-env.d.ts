/// <reference types="vite/client" />

// Declares the `*.css` side-effect import shape (among others) so `main.tsx` typechecks under
// `strict`. Without it `import "./styles/tokens.css"` is TS2882, and the honest fix is the
// reference rather than loosening the compiler.
