/* Decimal rounding shared by the document preview. Saved totals are validated on the server. */
(() => {
  const scaled = (value, places=6) => {
    const text=String(value || '0');
    if (!/^-?\d*(\.\d*)?$/.test(text)) return 0n;
    const [whole,fraction='']=text.replace('-','').split('.');
    return BigInt((whole || '0')+fraction.padEnd(places,'0').slice(0,places))*(text.startsWith('-')?-1n:1n);
  };
  const rounded=(n,d) => (n<0n?-1n:1n)*((n<0n?-n:n)+d/2n)/d;
  const cents=value=>rounded(scaled(value),10000n);
  const line=(price,qty)=>rounded(scaled(price)*scaled(qty),10000000000n);
  const tax=(net,rate)=>rounded(net*scaled(rate),100000000n);
  const text=c=>`${c<0n?'-':''}${(c<0n?-c:c)/100n}.${String((c<0n?-c:c)%100n).padStart(2,'0')}`;
  window.imsMoney={scaled,rounded,cents,line,tax,text};
})();
