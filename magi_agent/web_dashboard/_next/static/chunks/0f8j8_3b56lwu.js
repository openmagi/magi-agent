(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,19455,e=>{"use strict";var r=e.i(18050);let t={primary:"bg-primary text-white hover:bg-primary-light glow-sm hover:glow transition-all duration-200",secondary:"bg-transparent border border-black/10 text-foreground hover:border-primary/40 hover:bg-black/[0.04] transition-all duration-200",ghost:"bg-transparent text-secondary hover:text-foreground hover:bg-black/[0.04] transition-all duration-200",cta:"bg-cta text-white hover:bg-cta-light glow-cta transition-all duration-200"},i={sm:"px-4 py-2 text-sm rounded-lg gap-1.5 min-h-[44px]",md:"px-5 py-2.5 text-sm rounded-xl gap-2 min-h-[44px]",lg:"px-7 py-3.5 text-base rounded-xl gap-2 min-h-[44px]"};e.s(["Button",0,function({variant:e="primary",size:n="md",className:s="",disabled:o,...a}){return(0,r.jsx)("button",{className:`
        inline-flex items-center justify-center font-semibold cursor-pointer
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45
        focus-visible:ring-offset-2 focus-visible:ring-offset-background
        disabled:opacity-40 disabled:pointer-events-none
        ${t[e]}
        ${i[n]}
        ${s}
      `,disabled:o,...a})}])},59438,e=>{"use strict";var r=e.i(18050);e.s(["GlassCard",0,function({children:e,className:t="",hover:i=!1,glow:n=!1,onClick:s}){return(0,r.jsx)("div",{onClick:s,className:`
        glass rounded-2xl p-5
        ${i?"transition-all duration-200 hover:bg-glass-hover hover:border-primary/20 cursor-pointer":""}
        ${n?"glow-sm":""}
        ${t}
      `,children:e})}])},60119,e=>{"use strict";var r=e.i(18050),t=e.i(59438),i=e.i(19455);e.s(["default",0,function({error:e,reset:n}){return(0,r.jsx)("div",{className:"flex items-center justify-center min-h-[60vh]",children:(0,r.jsxs)(t.GlassCard,{className:"max-w-md text-center",children:[(0,r.jsx)("h2",{className:"text-xl font-bold text-foreground mb-4",children:"Something went wrong"}),(0,r.jsx)("p",{className:"text-secondary mb-6",children:e.message||"An unexpected error occurred."}),(0,r.jsx)(i.Button,{variant:"cta",size:"md",onClick:n,children:"Try again"})]})})}])}]);