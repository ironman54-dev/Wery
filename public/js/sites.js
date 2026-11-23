/* The product tabs in the top panel. To remove a tab: set  on:false  (or delete its line). To add one: add a line
   and a folder  /<id>/index.html  that loads  /js/cc-embed.js  with  data-site="<id>"  (copy greekwatch/index.html as a start).
   Tab colours live in the  html[data-site="<id>"]  rules in index.html. Nothing else needs touching. */
window.CC_SITES = [
  { id: "nexus",      label: "Nexus AI",   url: "/nexus/",      on: true, keepQuery: true },
  { id: "codeflow",   label: "Codeflow",   url: "/codeflow/",   on: true },
  { id: "greekwatch", label: "GreekWatch", url: "/greekwatch/", on: true }
];
