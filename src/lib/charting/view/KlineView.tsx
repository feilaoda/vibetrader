import PlotKline from "../plot/PlotKline"
import { ChartView, type ViewProps, type ViewState } from "./ChartView";
import { TVar } from "../../timeseris/TVar";
import { LINEAR_SCALAR } from "../scalar/LinearScala";
import { LG_SCALAR } from "../scalar/LgScalar";
import { Kline } from "../../domain/Kline";
import AxisY from "../pane/AxisY";
import PlotLine from "../plot/PlotLine";
import { type JSX } from "react";
import { LN_SCALAR } from "../scalar/LnScalar";
import { getCurrentSymbol, getMarket } from "../../domain/DataFecther";
import { splitCryptoSymbol } from "../../domain/CryptoSymbol";
import { Path } from "../../svg/Path";
import { Texts } from "../../svg/Texts";
import { stringMetrics } from "../../Utils";


export class KlineView extends ChartView<ViewProps, ViewState> {

    constructor(props: ViewProps) {
        super(props);

        this.yc.valueScalar = LINEAR_SCALAR;

        const { chartLines, chartAxisy, overlayChartLines, drawingLines } = this.plot();

        this.state = {
            ...this.state,
            chartLines,
            chartAxisy,
            overlayChartLines,
            drawingLines
        };
    }

    override plot() {
        this.computeGeometry();

        const chartLines = [
            <PlotKline
                kvar={this.props.tvar as TVar<Kline>}
                xc={this.props.xc}
                yc={this.yc}
                kind={this.props.xc.klineKind}
                depth={0}
            />
        ]

        const market = getMarket();
        const cryptoParts = market === 'crypto' ? splitCryptoSymbol(getCurrentSymbol()) : null;
        const formatCryptoPrice = (value: number) => {
            if (!Number.isFinite(value)) return "";
            const abs = Math.abs(value);
            if (abs >= 1000) return value.toFixed(2);
            if (abs >= 1) return value.toFixed(4);
            if (abs >= 0.01) return value.toFixed(6);
            if (abs >= 0.0001) return value.toFixed(8);
            return value.toPrecision(8);
        };

        const chartAxisy = <AxisY
            x={this.props.width - ChartView.AXISY_WIDTH}
            y={0}
            width={ChartView.AXISY_WIDTH}
            height={this.props.height}
            xc={this.props.xc}
            yc={this.yc}
            formatTick={market === 'crypto' ? formatCryptoPrice : undefined}
            unitLabel={market === 'crypto' ? cryptoParts?.quote : undefined}
        />

        const overlayChartLines = this.plotOverlayCharts();
        const drawingLines = this.plotDrawings()

        return { chartLines, chartAxisy, overlayChartLines, drawingLines }
    }

    protected override computeGeometry() {
        super.computeGeometry();
        if (getMarket() === 'crypto') {
            // Crypto prices should not be normalized/scaled (keep raw price for axis + cursor)
            this.yc.shouldNormScale = false;
            this.yc.normPow = 0;
            this.yc.normScale = 1;
            this.yc.normMultiple = "";
        }
    }

    override plotYValueLabel(y: number, value: number, className: string) {
        const market = getMarket();
        const parts = market === 'crypto' ? splitCryptoSymbol(getCurrentSymbol()) : null;
        const formatCryptoPrice = (v: number) => {
            if (!Number.isFinite(v)) return "";
            const abs = Math.abs(v);
            if (abs >= 1000) return v.toFixed(2);
            if (abs >= 1) return v.toFixed(4);
            if (abs >= 0.01) return v.toFixed(6);
            if (abs >= 0.0001) return v.toFixed(8);
            return v.toPrecision(8);
        };

        const displayValue = market === 'crypto' && this.yc.shouldNormScale
            ? value * this.yc.normScale
            : value;
        const valueStr = market === 'crypto'
            ? `${formatCryptoPrice(displayValue)}${parts?.quote ? ` ${parts.quote}` : ''}`
            : displayValue.toFixed(3);

        const metrics = stringMetrics(valueStr, this.font);
        const wLabel = metrics.width + 4;
        const hLabel = 13;
        const wAxisY = ChartView.AXISY_WIDTH;

        const axisyTexts = new Texts();
        const axisyPath = new Path();
        const y0 = y + 6;
        const x0 = 6;
        // draw arrow
        axisyPath.moveto(6, y - 3);
        axisyPath.lineto(0, y);
        axisyPath.lineto(6, y + 3);

        axisyPath.moveto(x0, y0);
        axisyPath.lineto(x0 + wLabel, y0);
        axisyPath.lineto(x0 + wLabel, y0 - hLabel);
        axisyPath.lineto(x0, y0 - hLabel);
        axisyPath.closepath();
        axisyTexts.text(8, y0 - 2, valueStr);

        const transformYAnnot = `translate(${this.props.width - wAxisY}, ${0})`;
        return (
            <g transform={transformYAnnot} className={className} style={{ fontSize: '12px' }}>
                {axisyPath.render()}
                {axisyTexts.render()}
            </g>
        );
    }

    protected override plotOverlayCharts() {
        const overlayChartLines: JSX.Element[] = []
        if (this.props.overlayIndicators) {
            let depth = 1;
            this.props.overlayIndicators.map((indicator, n) => {
                const tvar = indicator.tvar;
                for (const { title, atIndex, options: { style, color } } of indicator.outputs) {
                    let ovchart: JSX.Element;
                    switch (style) {
                        case "line":
                        default:
                            ovchart = <PlotLine
                                tvar={tvar}
                                name={title}
                                color={color}
                                atIndex={atIndex}
                                xc={this.props.xc}
                                yc={this.yc}
                                depth={depth++}
                            />
                    }

                    if (ovchart !== undefined) {
                        overlayChartLines.push(ovchart)
                    }
                }

            })
        }

        return overlayChartLines;
    }

    override computeMaxValueMinValue() {
        let max = Number.NEGATIVE_INFINITY;
        let min = Number.POSITIVE_INFINITY;

        const xc = this.props.xc;
        for (let i = 1; i <= xc.nBars; i++) {
            const time = xc.tb(i)
            if (xc.occurred(time)) {
                const kline = this.props.tvar.getByTime(time) as Kline;
                if (kline.close > 0) {
                    max = Math.max(max, kline.high)
                    min = Math.min(min, kline.low)
                }
            }
        }

        if (max == min) {
            max *= 1.05
            min *= 0.95
        }

        return [max, min]
    }

    swithScalarType() {
        switch (this.yc.valueScalar.kind) {
            case LINEAR_SCALAR.kind:
                this.yc.valueScalar = LG_SCALAR;
                break;

            case LG_SCALAR.kind:
                this.yc.valueScalar = LN_SCALAR;
                break;

            default:
                this.yc.valueScalar = LINEAR_SCALAR;
        }
    }

    override valueAtTime(time: number) {
        return (this.props.tvar.getByTime(time) as Kline).close;
    }

}
