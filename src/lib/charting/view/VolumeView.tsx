import { ChartView, type ViewProps, type ViewState } from "./ChartView";
import { TVar } from "../../timeseris/TVar";
import { LINEAR_SCALAR } from "../scalar/LinearScala";
import { LG_SCALAR } from "../scalar/LgScalar";
import { Kline } from "../../domain/Kline";
import AxisY from "../pane/AxisY";
import PlotVolmue from "../plot/PlotVolume";
import { Fragment } from "react/jsx-runtime";
import { getCurrentSymbol, getMarket } from "../../domain/DataFecther";
import { splitCryptoSymbol } from "../../domain/CryptoSymbol";
import { Path } from "../../svg/Path";
import { Texts } from "../../svg/Texts";
import { stringMetrics } from "../../Utils";

const formatVolumeAshare = (value: number) => {
    if (!Number.isFinite(value)) return "0";
    const abs = Math.abs(value);
    if (abs >= 1e9) return `${(value / 1e8).toFixed(2)}亿股`;
    if (abs >= 1e8) return `${(value / 1e7).toFixed(2)}千万股`;
    if (abs >= 1e7) return `${(value / 1e6).toFixed(2)}百万股`;
    if (abs >= 1e5) return `${(value / 1e4).toFixed(2)}万股`;
    return `${Math.round(value)}股`;
};

export class VolumeView extends ChartView<ViewProps, ViewState> {
    volumeUnitScale = 1;
    volumeUnitLabel = "";

    constructor(props: ViewProps) {
        super(props);

        const { chartLines, chartAxisy } = this.plot();

        this.state = {
            ...this.state,
            chartLines,
            chartAxisy,
        };

    }

    override plot() {
        this.computeGeometry();

        const chartLines = [
            <PlotVolmue
                kvar={this.props.tvar as TVar<Kline>}
                xc={this.props.xc}
                yc={this.yc}
                depth={0}
            />
        ]

        const chartAxisy = <AxisY
            x={this.props.width - ChartView.AXISY_WIDTH}
            y={0}
            width={ChartView.AXISY_WIDTH}
            height={this.props.height}
            xc={this.props.xc}
            yc={this.yc}
            formatTick={(v) => (this.volumeUnitScale ? (v / this.volumeUnitScale).toFixed(2) : v.toFixed(2))}
            unitLabel={this.volumeUnitLabel || undefined}
        />

        return { chartLines, chartAxisy }
    }

    override computeMaxValueMinValue() {
        let max = Number.NEGATIVE_INFINITY;
        const min = 0// Number.POSITIVE_INFINITY;

        const xc = this.props.xc;

        for (let i = 1; i <= xc.nBars; i++) {
            const time = xc.tb(i)
            if (xc.occurred(time)) {
                const kline = this.props.tvar.getByTime(time) as Kline;
                if (kline.close > 0) {
                    max = Math.max(max, kline.volume)
                }
            }
        }

        if (max === 0) {
            max = 1
        }

        const market = getMarket();
        if (market === 'ashare' || market === 'us') {
            if (max >= 1e9) {
                this.volumeUnitScale = 1e8;
                this.volumeUnitLabel = "亿股";
            } else if (max >= 1e8) {
                this.volumeUnitScale = 1e7;
                this.volumeUnitLabel = "千万股";
            } else if (max >= 1e7) {
                this.volumeUnitScale = 1e6;
                this.volumeUnitLabel = "百万股";
            } else if (max >= 1e5) {
                this.volumeUnitScale = 1e4;
                this.volumeUnitLabel = "万股";
            } else {
                this.volumeUnitScale = 1;
                this.volumeUnitLabel = "股";
            }
        } else {
            this.volumeUnitScale = 1;
            const parts = splitCryptoSymbol(getCurrentSymbol());
            this.volumeUnitLabel = parts?.base || "";
        }

        // if (max === min) {
        //   max *= 1.05
        //   min *= 0.95
        // }

        return [max, min]
    }

    swithScalarType() {
        switch (this.yc.valueScalar.kind) {
            case LINEAR_SCALAR.kind:
                this.yc.valueScalar = LG_SCALAR;
                break;

            default:
                this.yc.valueScalar = LINEAR_SCALAR;
        }
    }

    override valueAtTime(time: number) {
        return (this.props.tvar.getByTime(time) as Kline).volume
    }

    override plotYValueLabel(y: number, value: number, className: string) {
        const rawValue = this.yc.shouldNormScale ? value * this.yc.normScale : value;
        const market = getMarket();
        const parts = market === 'crypto' ? splitCryptoSymbol(getCurrentSymbol()) : null;
        const valueStr = market === 'ashare' || market === 'us'
            ? formatVolumeAshare(rawValue)
            : `${rawValue.toPrecision(8)}${parts?.base ? ` ${parts.base}` : ''}`;

        const metrics = stringMetrics(valueStr, this.font)
        const wLabel = metrics.width + 4
        const hLabel = 13;
        const wAxisY = ChartView.AXISY_WIDTH

        const axisyTexts = new Texts
        const axisyPath = new Path
        const y0 = y + 6
        const x0 = 6
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

        const transformYAnnot = `translate(${this.props.width - wAxisY}, ${0})`
        return (
            <g transform={transformYAnnot} className={className} style={{ fontSize: '12px' }}>
                {axisyPath.render()}
                {axisyTexts.render()}
            </g>
        );
    }

    render() {
        const transform = `translate(${this.props.x} ${this.props.y})`;
        return (
            <g transform={transform}>
                {this.state.chartLines.map((c, n) => <Fragment key={n}>{c}</Fragment>)}
                {this.state.chartAxisy}
                {this.state.referCursor}
                {this.state.mouseCursor}
            </g>
        )
    }
}
