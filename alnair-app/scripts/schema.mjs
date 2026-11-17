import fs from 'node:fs';import {z} from 'zod';import {componentSchema} from '../src/spec.ts';import {gallery} from '../src/demo.ts';
const outputs=[['../spec.schema.json',JSON.stringify(z.toJSONSchema(componentSchema),null,2)+'\n'],['../tests/gallery.json',JSON.stringify(gallery)]];
for(const [file,body] of outputs){const path=new URL(file,import.meta.url);if(process.argv.includes('--check')){if(fs.readFileSync(path,'utf8')!==body)throw Error(`${file} drifted; run npm run schema`)}else fs.writeFileSync(path,body)}
console.log(process.argv.includes('--check')?'Shared Rust/MCP/React schema and gallery fixtures match source':'Updated shared schema and gallery fixtures');
