"""Self-contained SW weekly-return heatmap; exact ranks and observations stay readable."""
import html


def rotation_chart(data):
    escape=lambda x:html.escape(str(x),quote=True)
    banner='<p><strong>SYNTHETIC 合成验收：非真实申万分类、行情或轮动结果。</strong></p>' if data['classification_basis']=='synthetic_fixture' else ''
    weeks=data['windows'];industries=data['industries'];width=240+120*len(weeks);height=85+34*len(industries)
    scale=max([abs(v['period_return']) for row in industries for v in row['observations']]+[.000001])
    parts=[];exact=[]
    for j,w in enumerate(weeks):parts.append(f'<text x="{240+120*j+57}" y="24" text-anchor="middle" font-size="13">{escape(w["week"])}</text>')
    for i,row in enumerate(industries):
        y=45+i*34;parts.append(f'<text x="6" y="{y+21}" font-size="14">{escape(row["name"])}</text>')
        for j,v in enumerate(row['observations']):
            x=240+120*j;ratio=abs(v['period_return'])/scale;light=98-45*ratio;hue=2 if v['period_return']>=0 else 155
            delta='—' if v['rank_change'] is None else f'{v["rank_change"]:+d}'
            note=f'{row["name"]} {v["week"]}: {v["anchor_date"]} → {v["last_trade_date"]}; return={v["period_return"]}; rank={v["rank"]}; change={delta}'
            parts.append(f'<g><title>{escape(note)}</title><rect x="{x}" y="{y}" width="114" height="32" rx="3" fill="hsl({hue},55%,{light}%)"/><text x="{x+57}" y="{y+14}" text-anchor="middle" font-size="13">{v["period_return"]*100:+.2f}%</text><text x="{x+57}" y="{y+29}" text-anchor="middle" font-size="11">#{v["rank"]} · Δ{delta}</text></g>')
            exact.append('<tr>'+''.join('<td>'+escape(x)+'</td>' for x in [row['industry_code'],row['index_symbol'],row['name'],v['week'],v['anchor_date'],v['last_trade_date'],v['period_return'],v['rank'],delta])+'</tr>')
    return '<section class="chart"><h3>申万2021一级行业：周收益与排名轮动</h3>'+banner+'<p>完整31行业 · 沪深 · 红色正收益，绿色负收益；每格为周收益、排名及较前周排名变化。正变化表示排名提升；首周未定义。颜色在整张图使用同一收益尺度，排名按收益保留12位后的竞争排名。展示顺序按最后一周排名。</p><p>分类来源：'+escape(data['classification_source'])+'；原始文件哈希：'+escape(data['document_sha256'])+'</p><div class="table-scroll"><svg viewBox="0 0 '+str(width)+' '+str(height)+'" style="width:'+str(width)+'px;max-width:none" role="img" aria-label="申万2021一级31行业周收益排名热力图">'+''.join(parts)+'</svg></div><details><summary>全部周收益与排名原始数值（收益为ratio）</summary><div class="table-scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in ['行业代码','指数','行业','周','锚点','最后交易日','周收益','排名','排名变化'])+'</tr></thead><tbody>'+''.join(exact)+'</tbody></table></div></details></section>'
